"""Version-bound delivery and provenance using real Work/protection persistence."""

from uuid import uuid4

import pytest

from agentguard.execution.models import StartRun
from agentguard.work.delivery import (
    AcceptSubmission,
    CreateSubmission,
    DeliveryService,
    ReviewSubmission,
    SaveRunArtifact,
)
from agentguard.work.models import ReviseRequirement, SaveArtifact
from agentguard.work.service import WorkError
from tests.test_execution_core import create_session, wait_for

pytest_plugins = ['tests.test_execution_core']


def finished(setup):
    runtime = setup['runtime']
    runtime.start()
    wait_for(lambda: runtime.accepting, bool)
    session = create_session(setup)
    with runtime.service() as service:
        run_id = service.start_run(setup['work_id'], session['id'], StartRun(request_id=uuid4(),
            expected_revision=session['revision'], requirement_version=1, input='Do work'))['run_id']
    runtime.notify()
    def read():
        with runtime.journal() as service:
            return service.one('work_runs', run_id)
    wait_for(read, lambda row: row['state'] == 'COMPLETED')
    return session, run_id


def test_output_promotion_submission_review_acceptance_and_changed_requirement(setup):
    session, run_id = finished(setup)
    work_id = setup['work_id']
    with setup['runtime'].service() as service:
        delivery = DeliveryService(service)
        artifact_command = SaveRunArtifact(request_id=uuid4(), expected_revision=2, title='Actual output')
        artifact = delivery.save_output(work_id, session['id'], run_id, artifact_command)
        assert delivery.save_output(work_id, session['id'], run_id, artifact_command) == artifact
        saved = service.work.artifact(work_id, artifact['artifact_id'], 1)
        assert saved['body'] == 'samesame'
        assert saved['source'] == 'AGENT_RUN' and saved['source_run_id'] == run_id
        run = service.run(work_id, session['id'], run_id)
        submit_command = CreateSubmission(request_id=uuid4(), expected_revision=artifact['revision'], requirement_version=1,
            run_id=run_id, responsibility='Implementer', artifact_refs=[{'artifact_id': artifact['artifact_id'], 'version': 1}],
            file_refs=[{'scope_id': setup['scope_id'], 'version_id': run['file_result']['version_id']}])
        submission = delivery.submit(work_id, submit_command)
        assert delivery.submit(work_id, submit_command) == submission
        review = delivery.decide(work_id, submission['submission_id'], ReviewSubmission(request_id=uuid4(),
            expected_revision=submission['revision'], decision='CHANGES_REQUESTED', body='More verification needed', reviewer='Reviewer'))
        accept_command = AcceptSubmission(request_id=uuid4(), expected_revision=review['revision'], decision='ACCEPTED', basis='Inspected fixed version', owner='Owner')
        acceptance = delivery.decide(work_id, submission['submission_id'], accept_command, acceptance=True)
        assert delivery.decide(work_id, submission['submission_id'], accept_command, acceptance=True) == acceptance
        assert delivery.submissions(work_id)['items'][0]['acceptances'][0]['decision'] == 'ACCEPTED'
        dependency = service.conn.execute("SELECT 1 FROM protection_version_dependencies WHERE owner_kind='submission' AND owner_id=? AND version_id=?",
                                          (submission['submission_id'], run['file_result']['version_id'])).fetchone()
        assert dependency
        revision = service.work.revise(work_id, ReviseRequirement(request_id=uuid4(), expected_revision=acceptance['revision'], body='New requirement'))['revision']
        current = delivery.submissions(work_id)['items'][0]
        assert not current['current_requirement']
        assert current['acceptances'][0]['decision'] == 'ACCEPTED'
        with pytest.raises(WorkError, match='WORK_REQUIREMENT_CHANGED'):
            delivery.decide(work_id, submission['submission_id'], AcceptSubmission(request_id=uuid4(), expected_revision=revision,
                decision='ACCEPTED', basis='Should be refused', owner='Owner'), acceptance=True)


def test_user_edited_output_and_manual_artifact_are_distinct(setup):
    session, run_id = finished(setup)
    with setup['runtime'].service() as service:
        delivery = DeliveryService(service)
        promoted = delivery.save_output(setup['work_id'], session['id'], run_id,
            SaveRunArtifact(request_id=uuid4(), expected_revision=2, title='Edited output', body='User correction'))
        artifact = service.work.artifact(setup['work_id'], promoted['artifact_id'], 1)
        assert artifact['source'] == 'USER_EDITED_RUN_OUTPUT'
        assert artifact['source_events']
        manual = service.work.save_artifact(setup['work_id'], SaveArtifact(request_id=uuid4(), expected_revision=promoted['revision'],
            requirement_version=1, title='Human note', body='Hand written'))
        assert service.work.artifact(setup['work_id'], manual['artifact_id'], 1)['source'] == 'MANUAL'


def test_delivery_cannot_use_wrong_work_or_invalid_versions(setup):
    session, run_id = finished(setup)
    with setup['runtime'].service() as service:
        delivery = DeliveryService(service)
        with pytest.raises(WorkError, match='SUBMISSION_EMPTY'):
            delivery.submit(setup['work_id'], CreateSubmission(request_id=uuid4(), expected_revision=2, requirement_version=1, responsibility='Owner'))
        with pytest.raises(WorkError, match='SUBMISSION_ARTIFACT_NOT_FOUND'):
            delivery.submit(setup['work_id'], CreateSubmission(request_id=uuid4(), expected_revision=2, requirement_version=1,
                responsibility='Owner', artifact_refs=[{'artifact_id': uuid4(), 'version': 1}]))
        with pytest.raises(WorkError, match='RUN_OUTPUT_SELECTION_INVALID'):
            delivery.save_output(setup['work_id'], session['id'], run_id, SaveRunArtifact(request_id=uuid4(),
                expected_revision=2, title='Bad selection', event_seqs=[1, 1]))


def test_unsubmitted_expired_history_is_summary_and_control_evidence_survives(setup):
    session, run_id = finished(setup)
    with setup['runtime'].service() as service:
        with service.db.transaction() as conn:
            conn.execute("UPDATE work_runs SET completed_at='2020-01-01T00:00:00+00:00' WHERE id=?", (run_id,))
            service.collect()
        run = service.run(setup['work_id'], session['id'], run_id)
        assert run['history_status'] == 'SUMMARY_ONLY' and 'input' not in run
        events = service.events(setup['work_id'], run_id)
        assert events['history_status'] == 'SUMMARY_ONLY'
        assert any(event['type'] == 'RUN_COMPLETED' for event in events['items'])
        assert not any(event['type'] == 'TEXT' for event in events['items'])
        assert service.conn.execute('SELECT input_json FROM work_runs WHERE id=?', (run_id,)).fetchone()[0] == '{}'


def test_submitted_run_is_not_pruned(setup):
    session, run_id = finished(setup)
    with setup['runtime'].service() as service:
        run = service.run(setup['work_id'], session['id'], run_id)
        DeliveryService(service).submit(setup['work_id'], CreateSubmission(request_id=uuid4(), expected_revision=2, requirement_version=1,
            run_id=run_id, responsibility='Owner', file_refs=[{'scope_id': setup['scope_id'], 'version_id': run['file_result']['version_id']}]))
        with service.db.transaction() as conn:
            conn.execute("UPDATE work_runs SET completed_at='2020-01-01T00:00:00+00:00' WHERE id=?", (run_id,))
            service.collect()
        assert service.one('work_runs', run_id)['history_status'] == 'FULL'


@pytest.mark.parametrize('submitted', [False, True])
def test_artifact_origin_survives_retention_even_without_submission_run_reference(setup, submitted):
    session, run_id = finished(setup)
    with setup['runtime'].service() as service:
        delivery = DeliveryService(service)
        artifact = delivery.save_output(setup['work_id'], session['id'], run_id,
            SaveRunArtifact(request_id=uuid4(), expected_revision=2, title='Retained output'))
        if submitted:
            delivery.submit(setup['work_id'], CreateSubmission(request_id=uuid4(), expected_revision=artifact['revision'],
                requirement_version=1, responsibility='Owner',
                artifact_refs=[{'artifact_id': artifact['artifact_id'], 'version': artifact['version']}]))
        with service.db.transaction() as conn:
            conn.execute("UPDATE work_runs SET completed_at='2020-01-01T00:00:00+00:00' WHERE id=?", (run_id,))
            service.collect()
        assert service.one('work_runs', run_id)['history_status'] == 'FULL'
        assert any(event['type'] == 'TEXT' for event in service.events(setup['work_id'], run_id)['items'])
        assert service.conn.execute("SELECT 1 FROM protection_version_dependencies WHERE owner_kind='run' AND owner_id=?",
                                    (run_id,)).fetchone()
