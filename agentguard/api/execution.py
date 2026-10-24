"""Authenticated local Work execution routes with cursor-based event streaming."""

import asyncio
import json
import sqlite3
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from agentguard.execution.models import (
    CancelPendingInstruction,
    CancelRun,
    ConnectSession,
    CreateSession,
    QueueInstruction,
    RenameSession,
    RespondRun,
    SessionMutation,
    StartRun,
)
from agentguard.execution.service import TERMINAL
from agentguard.work.delivery import (
    AcceptSubmission,
    CreateSubmission,
    DeliveryService,
    ReviewSubmission,
    SaveRunArtifact,
)
from agentguard.work.service import WorkError


def execution_router(runtime):
    root = APIRouter(prefix='/api/v1/works/{work_id}')
    router = APIRouter(prefix='/sessions')

    def invoke(method, *args, write=False, **kwargs):
        try:
            if write and not runtime.accepting:
                raise WorkError('EXECUTION_RUNTIME_UNAVAILABLE', 503)
            with runtime.service() as service:
                result = getattr(service, method)(*args, **kwargs)
            if write:
                runtime.notify()
            return result
        except (WorkError, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            code = getattr(error, 'code', getattr(error, 'reason_code', 'EXECUTION_STORAGE_UNAVAILABLE'))
            status = getattr(error, 'status', getattr(error, 'status_code', 503))
            return JSONResponse({'reason_code': code}, status_code=status)

    @router.get('')
    def sessions(work_id: UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return invoke('sessions', str(work_id), limit, offset)

    @root.get('/execution-summary')
    def execution_summary(work_id: UUID, limit: int = Query(20, ge=1, le=100)):
        return invoke('execution_summary', str(work_id), limit)

    @router.post('', status_code=201)
    def create(work_id: UUID, command: CreateSession):
        return invoke('create_session', str(work_id), command, write=True)

    @router.get('/{session_id}')
    def session(work_id: UUID, session_id: UUID):
        return invoke('session', str(work_id), str(session_id))

    @router.patch('/{session_id}')
    def rename(work_id: UUID, session_id: UUID, command: RenameSession):
        return invoke('mutate_session', str(work_id), str(session_id), command, write=True)

    @router.post('/{session_id}/archive')
    def archive(work_id: UUID, session_id: UUID, command: SessionMutation):
        result = invoke('mutate_session', str(work_id), str(session_id), command, archive=True, write=True)
        if not isinstance(result, JSONResponse):
            runtime.close_session(str(session_id))
        return result

    @router.post('/{session_id}/connect')
    def connect(work_id: UUID, session_id: UUID, command: ConnectSession):
        return invoke('connect', str(work_id), str(session_id), command, write=True)

    @router.get('/{session_id}/pending-instruction')
    def pending_instruction(work_id: UUID, session_id: UUID, request_id: UUID | None = None):
        return invoke('pending_instruction', str(work_id), str(session_id), request_id)

    @router.post('/{session_id}/pending-instruction', status_code=201)
    def enqueue_pending(work_id: UUID, session_id: UUID, command: QueueInstruction):
        return invoke('enqueue_pending', str(work_id), str(session_id), command, write=True)

    @router.post('/{session_id}/pending-instruction/{pending_id}/cancel')
    def cancel_pending(work_id: UUID, session_id: UUID, pending_id: UUID, command: CancelPendingInstruction):
        return invoke('cancel_pending', str(work_id), str(session_id), str(pending_id), command, write=True)

    @router.get('/{session_id}/runs')
    def runs(work_id: UUID, session_id: UUID, limit: int = Query(50, ge=1, le=100),
             offset: int = Query(0, ge=0), view: str = Query('summary', pattern='^(full|summary)$')):
        return invoke('runs', str(work_id), str(session_id), limit, offset, view)

    @router.post('/{session_id}/runs', status_code=201)
    def start_run(work_id: UUID, session_id: UUID, command: StartRun):
        return invoke('start_run', str(work_id), str(session_id), command, write=True)

    @router.get('/{session_id}/runs/{run_id}')
    def run(work_id: UUID, session_id: UUID, run_id: UUID, view: str = Query('full', pattern='^(full|summary)$')):
        return invoke('run', str(work_id), str(session_id), str(run_id), view)

    @router.get('/{session_id}/runs/{run_id}/events')
    async def events(work_id: UUID, session_id: UUID, run_id: UUID, request: Request,
                     after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100),
                     stream: bool = False):
        state = await asyncio.to_thread(invoke, 'run', str(work_id), str(session_id), str(run_id), 'summary')
        if isinstance(state, JSONResponse):
            return state
        if not stream and 'text/event-stream' not in request.headers.get('accept', ''):
            return await asyncio.to_thread(invoke, 'events', str(work_id), str(run_id), after, limit)

        async def iterate():
            cursor = after
            last_ping = asyncio.get_running_loop().time()
            while not await request.is_disconnected():
                page = await asyncio.to_thread(invoke, 'events', str(work_id), str(run_id), cursor, limit)
                if isinstance(page, JSONResponse):
                    yield 'event: error\ndata: {"reason_code":"EXECUTION_STREAM_UNAVAILABLE"}\n\n'
                    return
                for event in page['items']:
                    cursor = event['seq']
                    yield 'id: ' + str(cursor) + '\nevent: ' + event['type'] + '\ndata: ' + json.dumps(event, ensure_ascii=True) + '\n\n'
                if page['state'] in TERMINAL and not page['has_more']:
                    yield 'event: done\ndata: ' + json.dumps({'last_seq': page['last_seq'], 'history_status': page['history_status']}) + '\n\n'
                    return
                if asyncio.get_running_loop().time() - last_ping >= 15:
                    yield ': heartbeat\n\n'
                    last_ping = asyncio.get_running_loop().time()
                if not page['has_more']:
                    await asyncio.sleep(0.1)
        return StreamingResponse(iterate(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    @router.post('/{session_id}/runs/{run_id}/cancel')
    def cancel(work_id: UUID, session_id: UUID, run_id: UUID, command: CancelRun):
        return invoke('cancel', str(work_id), str(session_id), str(run_id), command, write=True)

    @router.post('/{session_id}/runs/{run_id}/artifacts', status_code=201)
    def save_output(work_id: UUID, session_id: UUID, run_id: UUID, command: SaveRunArtifact):
        return delivery_invoke(runtime, 'save_output', str(work_id), str(session_id), str(run_id), command)

    @router.post('/{session_id}/runs/{run_id}/responses')
    def respond(work_id: UUID, session_id: UUID, run_id: UUID, command: RespondRun):
        result = invoke('response', str(work_id), str(session_id), str(run_id), command, write=True)
        if not isinstance(result, JSONResponse):
            runtime._submit('response:' + str(run_id) + ':' + command.action_ref,
                            runtime.send_response, str(session_id), str(run_id), command)
        return result

    root.include_router(router)

    @root.get('/runs/{run_id}')
    def work_run(work_id: UUID, run_id: UUID, view: str = Query('full', pattern='^(full|summary)$')):
        return invoke('run', str(work_id), None, str(run_id), view)

    @root.get('/runs/{run_id}/events')
    async def work_events(work_id: UUID, run_id: UUID, request: Request, after: int = Query(0, ge=0),
                          limit: int = Query(100, ge=1, le=100), stream: bool = False):
        state = await asyncio.to_thread(invoke, 'run', str(work_id), None, str(run_id), 'summary')
        if isinstance(state, JSONResponse):
            return state
        return await events(work_id, UUID(state['session_id']), run_id, request, after, limit, stream)

    @root.post('/runs/{run_id}/cancel')
    def work_cancel(work_id: UUID, run_id: UUID, command: CancelRun):
        return invoke('cancel', str(work_id), None, str(run_id), command, write=True)

    @root.post('/runs/{run_id}/responses')
    def work_response(work_id: UUID, run_id: UUID, command: RespondRun):
        state = invoke('run', str(work_id), None, str(run_id), 'summary')
        if isinstance(state, JSONResponse):
            return state
        return respond(work_id, UUID(state['session_id']), run_id, command)

    @root.post('/runs/{run_id}/artifacts', status_code=201)
    def work_save_output(work_id: UUID, run_id: UUID, command: SaveRunArtifact):
        state = invoke('run', str(work_id), None, str(run_id), 'summary')
        if isinstance(state, JSONResponse):
            return state
        return save_output(work_id, UUID(state['session_id']), run_id, command)

    @root.post('/runs/{run_id}/reconcile')
    def reconcile(work_id: UUID, run_id: UUID, command: CancelRun):
        try:
            return runtime.reconcile(str(work_id), str(run_id), command)
        except (WorkError, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            return JSONResponse({'reason_code': getattr(error, 'code', getattr(error, 'reason_code', 'RUN_RECONCILIATION_UNAVAILABLE'))},
                                status_code=getattr(error, 'status', 503))

    return root


def delivery_invoke(runtime, method, *args, **kwargs):
    try:
        with runtime.service() as service:
            return getattr(DeliveryService(service), method)(*args, **kwargs)
    except (WorkError, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        code = getattr(error, 'code', getattr(error, 'reason_code', 'WORK_STORAGE_UNAVAILABLE'))
        return JSONResponse({'reason_code': code}, status_code=getattr(error, 'status', 503))


def delivery_router(runtime):
    router = APIRouter(prefix='/api/v1/works/{work_id}/submissions')

    @router.get('')
    def submissions(work_id: UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return delivery_invoke(runtime, 'submissions', str(work_id), limit, offset)

    @router.post('', status_code=201)
    def submit(work_id: UUID, command: CreateSubmission):
        return delivery_invoke(runtime, 'submit', str(work_id), command)

    @router.post('/{submission_id}/reviews', status_code=201)
    def review(work_id: UUID, submission_id: UUID, command: ReviewSubmission):
        return delivery_invoke(runtime, 'decide', str(work_id), str(submission_id), command)

    @router.post('/{submission_id}/acceptances', status_code=201)
    def accept(work_id: UUID, submission_id: UUID, command: AcceptSubmission):
        return delivery_invoke(runtime, 'decide', str(work_id), str(submission_id), command, acceptance=True)

    return router
