import { useEffect, useRef, useState } from 'react';
import * as THREE from '../vendor/three.module.js';
import { useI18n } from '../i18n/I18nProvider';
import { agentTint } from './workAppearance';
import './work-rest.css';

export type SceneAgent = {
  agent_id: string;
  detected_identity: string;
  lifecycle: string;
  observed_at?: string | null;
  uncertainty: boolean;
  association?: 'USER_LINKED' | null;
  activity?: 'UNKNOWN';
  presence?: 'CURRENT' | 'NOT_CURRENT' | 'STALE' | 'UNKNOWN';
  evidence_refs: string[];
};

export type SceneTask = {
  id: string;
  title: string;
  assignee_agent_ids?: string[];
};

export type WorkRestSceneProps = {
  title: string;
  requirementVersion: number;
  taskCount: number;
  artifactCount: number;
  updatedAt?: string;
  agents?: SceneAgent[];
  tasks?: SceneTask[];
  agentTints?: Record<string, string>;
  onAgentSelect?: (agentId: string) => void;
  onTaskSelect?: (taskId: string) => void;
  onManageAgents?: () => void;
  onReturn: () => void;
};

const MAX_BUBBLES = 4;
const SLOTS = [[-3.55, 1.32, -.2], [3.55, 1.23, -.1], [-3.6, -1.5, .1], [3.6, -1.42, .15]];
const TASK_SLOTS = [[-1.08, .78, .31], [1.08, .78, .31], [-1.08, -.78, .31], [1.08, -.78, .31]];

export function projectedLabelScale(radiusPixels: number, referencePixels: number) {
  return Math.max(.25, Math.min(1.4, radiusPixels / referencePixels));
}

function makeCoreGeometry() {
  const path = new THREE.Shape();
  const s = 1.71, r = .15;
  path.moveTo(-s + r, -s);
  path.lineTo(s - r, -s); path.quadraticCurveTo(s, -s, s, -s + r);
  path.lineTo(s, s - r); path.quadraticCurveTo(s, s, s - r, s);
  path.lineTo(-s + r, s); path.quadraticCurveTo(-s, s, -s, s - r);
  path.lineTo(-s, -s + r); path.quadraticCurveTo(-s, -s, -s + r, -s);
  const outline = path.getSpacedPoints(384).slice(0, -1)
    .map((p: { x: number; y: number }) => new THREE.Vector2((p.x - p.y) / Math.SQRT2, (p.x + p.y) / Math.SQRT2));
  const profile = [[0, -.16], [0, .025], [.10, .17], [.30, .245], [.69, .18], [.93, .045], [1, -.09], [1, -.23], [.82, -.27], [.15, -.26]];
  const positions: number[] = [], indices: number[] = [];
  outline.forEach((p: any, i: number) => {
    const width = .22 + .19 * Math.pow(Math.cos(Math.atan2(p.y, p.x) * 2), 10);
    const inside = p.clone().multiplyScalar(1 - width / p.length());
    profile.forEach(([k, z]) => positions.push(THREE.MathUtils.lerp(p.x, inside.x, k), THREE.MathUtils.lerp(p.y, inside.y, k), z));
    for (let j = 0; j < profile.length; j += 1) {
      const a = i * profile.length + j, b = ((i + 1) % outline.length) * profile.length + j;
      const c = ((i + 1) % outline.length) * profile.length + (j + 1) % profile.length, d = i * profile.length + (j + 1) % profile.length;
      indices.push(a, b, d, b, c, d);
    }
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices); geometry.computeVertexNormals();
  return { geometry, outline };
}

function makeEnvironment(renderer: any) {
  const width = 512, height = 256, data = new Float32Array(width * height * 4);
  for (let y = 0; y < height; y += 1) for (let x = 0; x < width; x += 1) {
    const u = x / width, v = y / height;
    const a = Math.exp(-(((u - .20) / .075) ** 8) - ((v - .32) / .21) ** 8) * 2.2;
    const b = Math.exp(-(((u - .68) / .14) ** 8) - ((v - .23) / .075) ** 8) * 1.7;
    const c = Math.exp(-(((u - .46) / .016) ** 4) - ((v - .49) / .30) ** 8) * 3;
    const d = Math.exp(-(((u - .91) / .05) ** 6) - ((v - .5) / .25) ** 8) * .75;
    const i = (y * width + x) * 4;
    data[i] = .085 + a + b * .83 + c + d * .58;
    data[i + 1] = .091 + a * .95 + b * .91 + c * .74 + d * .76;
    data[i + 2] = .104 + a * .88 + b + c * .48 + d;
    data[i + 3] = 1;
  }
  const source = new THREE.DataTexture(data, width, height, THREE.RGBAFormat, THREE.FloatType);
  source.mapping = THREE.EquirectangularReflectionMapping; source.needsUpdate = true;
  const generator = new THREE.PMREMGenerator(renderer);
  const target = generator.fromEquirectangular(source);
  generator.dispose(); source.dispose();
  return target;
}

function makeBubbleGeometry() {
  const geometry = new THREE.SphereGeometry(.71, 48, 36);
  const positions = geometry.attributes.position.array;
  for (let i = 0; i < positions.length; i += 3) {
    const x = positions[i] / .71, y = positions[i + 1] / .71, z = positions[i + 2] / .71;
    const shape = 1 + .09 * Math.sin(3.7 * x + 1.4 * y) * Math.cos(3.2 * y - 1.8 * z) + .045 * Math.sin(5 * z + 2 * x);
    positions[i] *= shape; positions[i + 1] *= shape; positions[i + 2] *= shape;
  }
  geometry.computeVertexNormals();
  return geometry;
}

function tintVector(tint: string) {
  return new THREE.Vector3(...[1, 3, 5].map(index => parseInt(tint.slice(index, index + 2), 16) / 255));
}

function makeBubbleMaterial(dim: number, tint: string) {
  return new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.FrontSide,
    uniforms: { time: { value: 0 }, dim: { value: dim }, tint: { value: tintVector(tint) } },
    vertexShader: `varying vec3 vN; varying vec3 vP; varying vec3 vO; uniform float time;
      void main() { vec3 p=position; p+=normal*sin(p.y*3.+time*.5)*sin(p.x*2.7-time*.24)*.014;
        vec4 w=modelMatrix*vec4(p,1.); vO=p; vP=w.xyz; vN=normalize(mat3(modelMatrix)*normal);
        gl_Position=projectionMatrix*viewMatrix*w; }`,
    fragmentShader: `precision highp float; varying vec3 vN; varying vec3 vP; varying vec3 vO; uniform float time; uniform float dim; uniform vec3 tint;
      void main() { vec3 n=normalize(vN), eye=normalize(cameraPosition-vP); float f=1.-max(dot(n,eye),0.);
        vec3 r=reflect(-eye,n); float flow=sin(vO.x*4.+sin(vO.y*5.)+time*.08)*.13+sin(vO.y*8.+vO.z*4.)*.04;
        vec3 film=.5+.5*cos(6.28318*(f*2.4+flow*1.5+.1+vec3(0.,.33,.67)));
        film=mix(film,tint,.48);
        float edge=pow(f,2.2); float band=pow(.5+.5*sin(f*28.+flow*24.),3.);
        float ribbon=exp(-pow((r.y-.55)/.11,2.))*smoothstep(-.7,.05,r.x);
        float soft=exp(-pow((r.x+.5)/.16,2.)-pow((r.y-.45)/.6,4.));
        float glint=pow(max(dot(r,normalize(vec3(-.4,.8,1.))),0.),110.);
        vec3 col=film*(.34+edge*1.5+band*edge*.75)+vec3(.66,.76,.85)*soft*.42
          +mix(vec3(.75,.82,1.),vec3(1.,.83,.63),f)*ribbon*.63+glint*.4;
        float alpha=.055+edge*.80+ribbon*.3+soft*.18+glint*.2;
        gl_FragColor=vec4(col*dim,clamp(alpha*dim,.025,.88)); }`,
  });
}

export default function WorkRestScene({ title, requirementVersion, taskCount, artifactCount, updatedAt,
  agents, tasks = [], agentTints, onAgentSelect, onTaskSelect, onManageAgents, onReturn }: WorkRestSceneProps) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const stage = useRef<HTMLDivElement>(null);
  const labels = useRef<Map<string, HTMLButtonElement>>(new Map());
  const taskLabels = useRef<Map<string, HTMLButtonElement>>(new Map());
  const coreLabel = useRef<HTMLDivElement>(null);
  const slotById = useRef<Map<string, number>>(new Map());
  const controls = useRef<{ toggle: () => void; reset: () => void; setTasks: (visible: boolean) => void;
    rotate: (yaw: number, pitch: number) => void; zoom: (delta: number) => void; setTints: () => void } | null>(null);
  const view = useRef({ yaw: 0, pitch: 0, distance: 9.8, initialized: false });
  const tints = useRef(agentTints); tints.current = agentTints;
  const [paused, setPaused] = useState(() => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || false);
  const pausedRef = useRef(paused);
  const [showTasks, setShowTasks] = useState(false);
  const tasksVisible = useRef(showTasks); tasksVisible.current = showTasks;
  const [unavailable, setUnavailable] = useState(false);
  const linkedAgents = (agents ?? []).filter(agent => agent.association === 'USER_LINKED')
    .sort((a, b) => a.agent_id.localeCompare(b.agent_id));
  const linkedIds = new Set(linkedAgents.map(agent => agent.agent_id));
  for (const id of slotById.current.keys()) if (!linkedIds.has(id)) slotById.current.delete(id);
  for (const agent of linkedAgents) {
    if (slotById.current.size === MAX_BUBBLES) break;
    if (slotById.current.has(agent.agent_id)) continue;
    const free = SLOTS.findIndex((_, index) => ![...slotById.current.values()].includes(index));
    slotById.current.set(agent.agent_id, free);
  }
  const visibleAgents = linkedAgents.filter(agent => slotById.current.has(agent.agent_id))
    .sort((a, b) => slotById.current.get(a.agent_id)! - slotById.current.get(b.agent_id)!);
  const agentIds = visibleAgents.map(agent => `${agent.agent_id}:${slotById.current.get(agent.agent_id)}:${agent.presence ?? 'CURRENT'}`).join('\u0000');
  const visibleTasks = tasks.slice(0, TASK_SLOTS.length);
  const sceneTaskIds = visibleTasks.map(task => `${task.id}:${(task.assignee_agent_ids ?? []).join(',')}`).join('\u0000');

  useEffect(() => {
    const host = stage.current;
    if (!host) return;
    let renderer: any;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' }); }
    catch { setUnavailable(true); return; }
    if (!renderer.getContext()) { renderer.dispose(); setUnavailable(true); return; }
    setUnavailable(false);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.22;
    renderer.setClearColor(0x101619, 0);
    renderer.domElement.setAttribute('aria-hidden', 'true');
    host.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const environment = makeEnvironment(renderer);
    scene.environment = environment.texture;
    const camera = new THREE.PerspectiveCamera(38, 1, .1, 100);
    const world = new THREE.Group(); scene.add(world);
    const coreGroup = new THREE.Group();
    coreGroup.rotation.set(.055, -.17, -.026); world.add(coreGroup);
    const { geometry: coreGeometry, outline } = makeCoreGeometry();
    const coreMaterial = new THREE.MeshPhysicalMaterial({ color: 0xe3d4bc, metalness: .82, roughness: .22,
      clearcoat: .95, clearcoatRoughness: .14, envMapIntensity: 1.55, side: THREE.DoubleSide });
    coreGroup.add(new THREE.Mesh(coreGeometry, coreMaterial));

    const insetPositions = [0, 0, -.245];
    const insetIndices: number[] = [];
    outline.forEach((p: any, i: number) => {
      const width = .22 + .19 * Math.pow(Math.cos(Math.atan2(p.y, p.x) * 2), 10);
      const k = 1 - width / p.length();
      insetPositions.push(p.x * k, p.y * k, -.245);
      insetIndices.push(0, i + 1, ((i + 1) % outline.length) + 1);
    });
    const insetGeometry = new THREE.BufferGeometry();
    insetGeometry.setAttribute('position', new THREE.Float32BufferAttribute(insetPositions, 3));
    insetGeometry.setIndex(insetIndices); insetGeometry.computeVertexNormals();
    const insetMaterial = new THREE.MeshPhysicalMaterial({ color: 0x355154, metalness: .32, roughness: .58,
      transparent: true, opacity: .30, side: THREE.DoubleSide, depthWrite: false });
    coreGroup.add(new THREE.Mesh(insetGeometry, insetMaterial));

    const rims: any[] = [];
    for (const [k, z, color, opacity] of [[.07, .118, 0xffebca, .70], [.93, .05, 0xa8e0d8, .60], [.98, -.20, 0x788b8c, .35]]) {
      const points = outline.map((p: any) => {
        const width = .22 + .19 * Math.pow(Math.cos(Math.atan2(p.y, p.x) * 2), 10);
        return new THREE.Vector3(p.x * (1 - k * width / p.length()), p.y * (1 - k * width / p.length()), z);
      });
      points.push(points[0]);
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),
        new THREE.LineBasicMaterial({ color, transparent: true, opacity }));
      coreGroup.add(line); rims.push(line);
    }

    const bubbleGeometry = makeBubbleGeometry();
    const bubbleMaterials: any[] = [];
    const bubbleCenters = visibleAgents.map(agent => {
      const position = SLOTS[slotById.current.get(agent.agent_id)!];
      const material = makeBubbleMaterial(agent.presence === 'NOT_CURRENT' || agent.presence === 'STALE' || agent.presence === 'UNKNOWN' ? .48 : 1, agentTint(agent.agent_id, tints.current));
      bubbleMaterials.push(material);
      const mesh = new THREE.Mesh(bubbleGeometry, material);
      mesh.position.set(position[0], position[1], position[2]);
      mesh.rotation.set(.12, .17, -.13);
      world.add(mesh);
      return { id: agent.agent_id, point: new THREE.Vector3(position[0], position[1], position[2]) };
    });
    const taskGroup = new THREE.Group(); taskGroup.visible = tasksVisible.current; world.add(taskGroup);
    const taskGeometry = new THREE.BufferGeometry();
    taskGeometry.setAttribute('position', new THREE.Float32BufferAttribute([
      0, .13, 0, .13, 0, 0, 0, -.13, 0, -.13, 0, 0,
    ], 3));
    taskGeometry.setIndex([0, 1, 2, 0, 2, 3]);
    taskGeometry.computeVertexNormals();
    const taskMaterial = new THREE.MeshPhysicalMaterial({ color: 0xd1d9c5, metalness: .45,
      roughness: .3, side: THREE.DoubleSide, envMapIntensity: 1.2 });
    const spokeMaterial = new THREE.LineBasicMaterial({ color: 0x9bb2a7, transparent: true, opacity: .32 });
    const relationMaterial = new THREE.LineBasicMaterial({ color: 0xb5d6c2, transparent: true, opacity: .52 });
    const taskLines: any[] = [];
    const taskCenters = visibleTasks.map((task, index) => {
      const slot = TASK_SLOTS[index], point = new THREE.Vector3(slot[0], slot[1], slot[2]);
      const marker = new THREE.Mesh(taskGeometry, taskMaterial); marker.position.copy(point); taskGroup.add(marker);
      const spoke = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, .26), point]), spokeMaterial);
      taskGroup.add(spoke); taskLines.push(spoke);
      const assignedIds = new Set(task.assignee_agent_ids ?? []);
      for (const bubble of bubbleCenters) {
        if (!assignedIds.has(bubble.id)) continue;
        const link = new THREE.Line(new THREE.BufferGeometry().setFromPoints([point, bubble.point]), relationMaterial);
        taskGroup.add(link); taskLines.push(link);
      }
      return { id: task.id, point };
    });

    let { yaw, pitch, distance } = view.current;
    let motionTime = 0, last = performance.now(), frameId = 0;
    let isPaused = pausedRef.current, contextLost = false;
    const motionPreference = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    let drag: { x: number; y: number } | null = null;
    setPaused(isPaused);
    const draw = () => {
      view.current = { yaw, pitch, distance, initialized: true };
      world.rotation.set(pitch, yaw, 0);
      camera.position.set(0, 0, distance); camera.lookAt(0, 0, 0); camera.updateMatrixWorld();
      scene.updateMatrixWorld();
      const project = (point: any) => point.clone().project(camera);
      const screenDistance = (a: any, b: any) => Math.hypot(
        (a.x - b.x) * host.clientWidth * .5, (a.y - b.y) * host.clientHeight * .5);
      const center = project(coreGroup.localToWorld(new THREE.Vector3(0, 0, .26)));
      const coreRadius = Math.max(
        screenDistance(center, project(coreGroup.localToWorld(new THREE.Vector3(1.8, 0, .26)))),
        screenDistance(center, project(coreGroup.localToWorld(new THREE.Vector3(0, 1.8, .26)))));
      if (coreLabel.current) {
        coreLabel.current.style.left = `${(center.x * .5 + .5) * host.clientWidth}px`;
        coreLabel.current.style.top = `${(-center.y * .5 + .5) * host.clientHeight}px`;
        const scale = projectedLabelScale(coreRadius, 150);
        coreLabel.current.style.setProperty('--scene-scale', String(scale));
        coreLabel.current.style.setProperty('--scene-font-floor', `${9 / scale}px`);
        coreLabel.current.classList.toggle('is-compact', scale <= .55);
      }
      const occupied: { left: number; top: number; right: number; bottom: number }[] = [];
      const taskPanel = host.querySelector<HTMLElement>('.work-rest-task-list');
      if (taskPanel) occupied.push({ left: taskPanel.offsetLeft, top: taskPanel.offsetTop,
        right: taskPanel.offsetLeft + taskPanel.offsetWidth, bottom: taskPanel.offsetTop + taskPanel.offsetHeight });
      let crowded = false;
      for (const bubble of bubbleCenters) {
        const label = labels.current.get(bubble.id);
        if (!label) continue;
        const point = project(world.localToWorld(bubble.point.clone()));
        const edge = project(world.localToWorld(bubble.point.clone().add(new THREE.Vector3(0, .71, 0))));
        const scale = projectedLabelScale(screenDistance(point, edge), 72);
        label.style.left = `${(point.x * .5 + .5) * host.clientWidth}px`;
        label.style.top = `${(-point.y * .5 + .5) * host.clientHeight}px`;
        label.style.setProperty('--scene-scale', String(scale));
        label.style.setProperty('--scene-font-floor', `${8 / scale}px`);
        label.classList.toggle('is-compact', scale <= .65);
        label.classList.toggle('is-tiny', scale <= .5);
        // Crowded or off-camera projections retain a readable list alternative.
        const width = Math.min(160, Math.max(110, host.clientWidth * .20)) * scale;
        const height = label.offsetHeight * scale;
        const left = Math.max(8, Math.min(host.clientWidth - width - 8, (point.x * .5 + .5) * host.clientWidth - width / 2));
        const top = Math.max(8, Math.min(host.clientHeight - height - 8, (-point.y * .5 + .5) * host.clientHeight + 47 * scale));
        const box = { left, top, right: left + width, bottom: top + height };
        const hidden = scale < .65 || point.z < -1 || point.z > 1 || host.clientWidth < width + 16 ||
          occupied.some(other => box.left < other.right + 6 && box.right > other.left - 6 && box.top < other.bottom + 6 && box.bottom > other.top - 6);
        label.style.visibility = hidden ? 'hidden' : '';
        label.style.left = `${left + width / 2}px`;
        label.style.top = `${top - 47 * scale}px`;
        if (hidden) crowded = true; else occupied.push(box);
      }
      host.parentElement?.classList.toggle('has-crowded-labels', crowded);
      for (const task of taskCenters) {
        const label = taskLabels.current.get(task.id);
        if (!label) continue;
        const point = project(world.localToWorld(task.point.clone()));
        label.style.left = `${(point.x * .5 + .5) * host.clientWidth}px`;
        label.style.top = `${(-point.y * .5 + .5) * host.clientHeight}px`;
        label.style.setProperty('--scene-scale', String(projectedLabelScale(coreRadius, 150)));
      }
      renderer.render(scene, camera);
    };
    const frame = (now: number) => {
      frameId = 0;
      if (isPaused || document.hidden || contextLost) return;
      motionTime += Math.min((now - last) / 1000, .05); last = now;
      for (const material of bubbleMaterials) material.uniforms.time.value = motionTime;
      draw(); frameId = requestAnimationFrame(frame);
    };
    let previousFit = Math.max(9.8, 12.7 / Math.max(.54, Math.max(1, host.clientWidth) / Math.max(1, host.clientHeight)));
    const resize = () => {
      const width = Math.max(1, host.clientWidth), height = Math.max(1, host.clientHeight);
      renderer.setSize(width, height); camera.aspect = width / height;
      const fitted = Math.max(9.8, 12.7 / Math.max(.54, camera.aspect));
      distance = THREE.MathUtils.clamp(distance * fitted / previousFit, Math.max(7.4, 12.7 / Math.max(.54, camera.aspect)), 32);
      previousFit = fitted;
      camera.updateProjectionMatrix(); draw();
    };
    const reset = () => {
      yaw = 0; pitch = 0;
      distance = Math.max(9.8, 12.7 / Math.max(.54, Math.max(1, host.clientWidth) / Math.max(1, host.clientHeight)));
      draw();
    };
    const toggle = () => {
      isPaused = !isPaused; pausedRef.current = isPaused; setPaused(isPaused);
      if (isPaused) { cancelAnimationFrame(frameId); frameId = 0; draw(); }
      else if (!document.hidden && !contextLost) { last = performance.now(); frameId = requestAnimationFrame(frame); }
    };
    const rotate = (horizontal: number, vertical: number) => {
      yaw += horizontal; pitch = THREE.MathUtils.clamp(pitch + vertical, -.65, .65); draw();
    };
    const zoom = (delta: number) => {
      distance = THREE.MathUtils.clamp(distance + delta, Math.max(7.4, 12.7 / Math.max(.54, camera.aspect)), 32); draw();
    };
    controls.current = { toggle, reset, rotate, zoom, setTasks: visible => { taskGroup.visible = visible; draw(); },
      setTints: () => { visibleAgents.forEach((agent, index) => bubbleMaterials[index].uniforms.tint.value.copy(tintVector(agentTint(agent.agent_id, tints.current)))); draw(); } };
    const preferenceChanged = (event: MediaQueryListEvent) => {
      if (!event.matches) return;
      isPaused = true; pausedRef.current = true; setPaused(true); cancelAnimationFrame(frameId); frameId = 0; draw();
    };
    const visibilityChanged = () => {
      cancelAnimationFrame(frameId); frameId = 0;
      if (!document.hidden && !isPaused && !contextLost) { last = performance.now(); frameId = requestAnimationFrame(frame); }
    };
    motionPreference?.addEventListener?.('change', preferenceChanged);
    document.addEventListener('visibilitychange', visibilityChanged);
    const down = (event: globalThis.PointerEvent) => {
      drag = { x: event.clientX, y: event.clientY };
      renderer.domElement.setPointerCapture(event.pointerId);
    };
    const move = (event: globalThis.PointerEvent) => {
      if (!drag) return;
      yaw += (event.clientX - drag.x) * .004;
      pitch = THREE.MathUtils.clamp(pitch + (event.clientY - drag.y) * .003, -.65, .65);
      drag = { x: event.clientX, y: event.clientY }; draw();
    };
    const up = () => { drag = null; };
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      zoom(event.deltaY * .007);
    };
    const lost = (event: Event) => { event.preventDefault(); contextLost = true; setUnavailable(true); cancelAnimationFrame(frameId); frameId = 0; };
    const restored = () => {
      contextLost = false; setUnavailable(false); resize();
      if (!isPaused && !document.hidden) { last = performance.now(); frameId = requestAnimationFrame(frame); }
    };
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : null;
    observer?.observe(host); window.addEventListener('resize', resize);
    renderer.domElement.addEventListener('pointerdown', down);
    renderer.domElement.addEventListener('pointermove', move);
    renderer.domElement.addEventListener('pointerup', up);
    renderer.domElement.addEventListener('pointercancel', up);
    renderer.domElement.addEventListener('wheel', wheel, { passive: false });
    renderer.domElement.addEventListener('webglcontextlost', lost);
    renderer.domElement.addEventListener('webglcontextrestored', restored);
    if (!view.current.initialized) reset();
    resize();
    if (!isPaused && !document.hidden) frameId = requestAnimationFrame(frame);
    return () => {
      controls.current = null; cancelAnimationFrame(frameId); observer?.disconnect();
      motionPreference?.removeEventListener?.('change', preferenceChanged);
      document.removeEventListener('visibilitychange', visibilityChanged);
      host.parentElement?.classList.remove('has-crowded-labels');
      window.removeEventListener('resize', resize);
      renderer.domElement.removeEventListener('pointerdown', down);
      renderer.domElement.removeEventListener('pointermove', move);
      renderer.domElement.removeEventListener('pointerup', up);
      renderer.domElement.removeEventListener('pointercancel', up);
      renderer.domElement.removeEventListener('wheel', wheel);
      renderer.domElement.removeEventListener('webglcontextlost', lost);
      renderer.domElement.removeEventListener('webglcontextrestored', restored);
      coreGeometry.dispose(); coreMaterial.dispose(); insetGeometry.dispose(); insetMaterial.dispose();
      bubbleGeometry.dispose(); for (const material of bubbleMaterials) material.dispose();
      taskGeometry.dispose(); taskMaterial.dispose(); spokeMaterial.dispose(); relationMaterial.dispose();
      for (const line of taskLines) line.geometry.dispose();
      for (const rim of rims) { rim.geometry.dispose(); rim.material.dispose(); }
      environment.dispose(); scene.environment = null; renderer.dispose(); renderer.domElement.remove();
    };
  }, [agentIds, sceneTaskIds]);

  useEffect(() => { if (!unavailable) controls.current?.setTasks(showTasks); }, [showTasks, agentIds, sceneTaskIds, unavailable]);
  useEffect(() => { controls.current?.setTints(); }, [agentTints]);

  const date = updatedAt ? new Date(updatedAt) : null;
  const dateText = date && !Number.isNaN(date.getTime()) ? date.toLocaleString(locale) : null;
  const agentStatus = (agent: SceneAgent) => agent.presence === 'NOT_CURRENT'
    ? (zh ? '本次未观测 · 活动未知' : 'Not observed now · Activity unknown')
    : agent.presence === 'STALE' ? (zh ? '观测已过期 · 活动未知' : 'Observation expired · Activity unknown')
    : agent.presence === 'UNKNOWN' ? (zh ? '是否仍在运行未知 · 活动未知' : 'Presence unknown · Activity unknown')
    : `${agent.lifecycle} · ${zh ? '活动未知' : 'Activity unknown'}${agent.uncertainty ? (zh ? ' · 身份待确认' : ' · Identity uncertain') : ''}`;
  const agentButton = (agent: SceneAgent, projected: boolean) => <button key={agent.agent_id}
    className={`${projected ? 'work-rest-agent-label' : 'work-rest-agent-row'}${agent.presence === 'NOT_CURRENT' || agent.presence === 'STALE' || agent.presence === 'UNKNOWN' ? ' is-not-current' : ''}`}
    ref={projected ? node => { if (node) labels.current.set(agent.agent_id, node); else labels.current.delete(agent.agent_id); } : undefined}
    onClick={() => onAgentSelect?.(agent.agent_id)} disabled={!onAgentSelect}
    aria-label={`${agent.detected_identity} ${agent.agent_id.slice(-6)}, ${agentStatus(agent)}`}
    title={`${agent.detected_identity} · ${agent.agent_id.slice(-6)} · ${agentStatus(agent)}`}>
    <span className="work-rest-agent-name">
      <span className="work-rest-agent-name-full">{agent.detected_identity} · {agent.agent_id.slice(-6)}</span>
      <span className="work-rest-agent-name-tiny">#{agent.agent_id.slice(-6)}</span>
    </span>
    <span className="work-rest-agent-status">{agentStatus(agent)}</span>
  </button>;
  const assignedAgentLabel = (id: string) => {
    const agent = linkedAgents.find(item => item.agent_id === id);
    return agent ? `${agent.detected_identity} · ${id.slice(-6)}` : id.slice(-8);
  };

  return <section className={`work-rest${unavailable ? ' is-unavailable' : ''}${linkedAgents.length ? ' has-agents' : ''}${linkedAgents.length > MAX_BUBBLES ? ' has-agent-overflow' : ''}`}
    aria-label={zh ? '工作空间观赏视图' : 'Workspace scene'}>
    <header className="work-rest-header">
      <button autoFocus className="work-rest-back" onClick={onReturn}
        aria-label={zh ? '返回工作空间' : 'Return to workspace'} title={zh ? '返回工作空间' : 'Return to workspace'}>
        <span className="work-rest-back-icon" aria-hidden="true">←</span>
        <span className="work-rest-back-text">{zh ? '返回工作空间' : 'Return to workspace'}</span>
      </button>
      <div className="work-rest-controls">
        {tasks.length > 0 && <button onClick={() => setShowTasks(current => !current)} aria-pressed={showTasks}>
          {zh ? '任务关联' : 'Task links'}
        </button>}
        {!unavailable && <><button onClick={() => controls.current?.toggle()} aria-pressed={paused}
          aria-label={paused ? (zh ? '继续动态' : 'Resume motion') : (zh ? '暂停动态' : 'Pause motion')}
          title={paused ? (zh ? '继续动态' : 'Resume motion') : (zh ? '暂停动态' : 'Pause motion')}>
          <span aria-hidden="true">{paused ? '▶' : 'Ⅱ'}</span>
        </button>
          <button onClick={() => controls.current?.reset()} aria-label={zh ? '复位视角' : 'Reset view'}
            title={zh ? '复位视角' : 'Reset view'}><span aria-hidden="true">↺</span></button></>}
      </div>
    </header>
    <div className="work-rest-stage" ref={stage} tabIndex={unavailable ? undefined : 0}
      role={unavailable ? undefined : 'group'} aria-label={zh ? '三维场景：方向键旋转，加减键缩放，Home 复位' : '3D scene: Arrow keys rotate, plus and minus zoom, Home resets'}
      onKeyDown={event => {
        if (event.target !== event.currentTarget) return;
        const rotations: Record<string, [number, number]> = { ArrowLeft: [-.12, 0], ArrowRight: [.12, 0], ArrowUp: [0, -.10], ArrowDown: [0, .10] };
        if (rotations[event.key]) { event.preventDefault(); controls.current?.rotate(...rotations[event.key]); }
        else if (event.key === '+' || event.key === '=') { event.preventDefault(); controls.current?.zoom(-1); }
        else if (event.key === '-') { event.preventDefault(); controls.current?.zoom(1); }
        else if (event.key === 'Home') { event.preventDefault(); controls.current?.reset(); }
      }}>
      {!unavailable && <div className="work-rest-core-label" ref={coreLabel} aria-hidden="true">
        <span>WORK</span><strong>{title}</strong>
      </div>}
      {!unavailable && <p className="work-rest-keyboard-hint">{zh ? '拖动旋转 · 滚动缩放 · 键盘：方向键、+/−、Home' : 'Drag to rotate · Scroll to zoom · Keyboard: arrows, +/−, Home'}</p>}
      {!unavailable && <div className="work-rest-agent-labels">{visibleAgents.map(agent => agentButton(agent, true))}</div>}
      {!unavailable && showTasks && <div className="work-rest-task-markers">
        {visibleTasks.map((task, index) => <button key={task.id} className="work-rest-task-marker"
          ref={node => { if (node) taskLabels.current.set(task.id, node); else taskLabels.current.delete(task.id); }}
          aria-label={`${zh ? '打开任务' : 'Open task'} ${index + 1}: ${task.title}`}
          onClick={() => onTaskSelect?.(task.id)} disabled={!onTaskSelect}>{index + 1}</button>)}
      </div>}
      {linkedAgents.length === 0 && !showTasks && <div className="work-rest-empty">
        <span>{zh ? '暂无关联的实际 Agent' : 'No linked agents'}</span>
        {onManageAgents && <button onClick={onManageAgents}>{zh ? '关联实际 Agent' : 'Link an actual agent'}</button>}
      </div>}
      {showTasks && <div className="work-rest-task-list">
        <div className="work-rest-task-list-head">
          <span className="work-rest-task-list-title">{zh ? '任务关联' : 'Task links'}</span>
          {linkedAgents.length === 0 && onManageAgents && <button onClick={onManageAgents}>
            {zh ? '关联 Agent' : 'Link agent'}
          </button>}
        </div>
        {tasks.map((task, index) => <button key={task.id} className="work-rest-task-row"
          onClick={() => onTaskSelect?.(task.id)} disabled={!onTaskSelect}>
          <span className="work-rest-task-title"><b>{String(index + 1).padStart(2, '0')}</b>{task.title}</span>
          <span className="work-rest-task-assignees">{(task.assignee_agent_ids?.length ?? 0) > 0
            ? task.assignee_agent_ids!.map(assignedAgentLabel).join(' · ')
            : (zh ? '未关联 Agent' : 'No linked agent')}</span>
        </button>)}
      </div>}
    </div>
    <footer className="work-rest-facts">
      <div className="work-rest-work">
        <span className="work-rest-kicker">{zh ? '当前工作空间' : 'CURRENT WORKSPACE'}</span>
        <h1>{title}</h1>
        {dateText && <span className="work-rest-date">{zh ? '最近更新' : 'Updated'} · {dateText}</span>}
      </div>
      <dl>
        <div><dt>{zh ? '要求版本' : 'Requirement'}</dt><dd>v{requirementVersion}</dd></div>
        <div><dt>{zh ? '任务' : 'Tasks'}</dt><dd>{taskCount}</dd></div>
        <div><dt>{zh ? '成果' : 'Artifacts'}</dt><dd>{artifactCount}</dd></div>
        <div><dt>{zh ? '关联 Agent' : 'Linked agents'}</dt><dd>{linkedAgents.length}</dd></div>
      </dl>
      {onManageAgents && linkedAgents.length > 0 && <button className="work-rest-manage" onClick={onManageAgents}>
        {linkedAgents.length > MAX_BUBBLES ? (zh ? `查看全部 ${linkedAgents.length} 个 Agent` : `View all ${linkedAgents.length} agents`) : (zh ? '管理关联' : 'Manage links')}
      </button>}
      {unavailable && <p role="status" className="work-rest-fallback">{zh ? '此设备无法显示 3D 场景。工作信息和 Agent 仍可查看。' : '3D is unavailable on this device. Work and agent details remain available.'}</p>}
    </footer>
    {linkedAgents.length > 0 && <div className="work-rest-mobile-agents" aria-label={zh ? '关联 Agent' : 'Linked agents'}>
      {linkedAgents.map(agent => agentButton(agent, false))}
    </div>}
  </section>;
}
