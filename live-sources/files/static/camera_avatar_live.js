/* Live camera view: renders what one registered camera sees, with a 3D GLB avatar standing in front of it.
 *
 *   /static/camera_avatar_live.html?camera=wide_shot&avatar=/assets/avatar/manny.glb&publish=1
 *
 * Every tick it asks the host (/api/camera-view/state/<camera>) for the camera's authoritative pose and the avatar's
 * motion, aims a perspective camera from that pose, applies the motion to the avatar's bones through the existing
 * PubcastGLBMotionConsumer, and draws. With publish=1 each drawn frame is also sent as a JPEG to the existing
 * /api/studio25d/media/frames/<camera> endpoint, which is how the rest of PubCast sees real camera pixels.
 *
 * Pose convention (see modules/camera_view_routes.py): rotation = [tilt, pan, roll] degrees, a camera at rotation
 * [0,0,0] looks along -z, tilt up is positive, pan right is positive. The avatar stands at the origin facing +z.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const params = new URLSearchParams(location.search);
const cameraId = params.get("camera") || "wide_shot";
const avatarUrl = params.get("avatar") || "/assets/avatar/manny.glb";
const avatarId = params.get("avatar_id") || "avatar";
const publish = params.get("publish") === "1";
const WIDTH = 640, HEIGHT = 360, POLL_MS = 50, PUBLISH_MS = 250;
const DEG = Math.PI / 180;

const canvas = document.getElementById("view");
const hud = document.getElementById("hud");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
renderer.setSize(WIDTH, HEIGHT, false);
renderer.outputColorSpace = THREE.SRGBColorSpace;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x10151f);
scene.add(new THREE.HemisphereLight(0xfff2e0, 0x3a3f55, 1.7));
const key = new THREE.DirectionalLight(0xffe2b8, 2.4); key.position.set(2, 4, 4); scene.add(key);
const floor = new THREE.Mesh(new THREE.CircleGeometry(6, 64), new THREE.MeshStandardMaterial({ color: 0x232b3b, roughness: 0.95 }));
floor.rotation.x = -Math.PI / 2; scene.add(floor);
const grid = new THREE.GridHelper(12, 24, 0x3d4b6b, 0x2a3350); grid.position.y = 0.002; scene.add(grid);

const camera = new THREE.PerspectiveCamera(50, WIDTH / HEIGHT, 0.05, 200);
camera.rotation.order = "YXZ";

const debug = {
  cameraId, avatarUrl, loaded: false, error: "", ticks: 0, frames: 0, published: 0, publishErrors: 0,
  lastPose: null, lastMotion: null, repairedBufferViews: 0, applied: 0, lastPublish: null, mapped: [],
};
window.__camView = debug;

function applyPose(pose) {
  const [px, py, pz] = pose.position;
  const [tilt, pan, roll] = pose.rotation;
  camera.position.set(px, py, pz);
  camera.rotation.set(tilt * DEG, -pan * DEG, roll * DEG);
  camera.fov = pose.fov;
  camera.updateProjectionMatrix();
  debug.lastPose = pose;
}

async function loadAvatar() {
  const res = await fetch(avatarUrl, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`avatar file ${res.status}`);
  const fixed = window.PubCastGlbBufferRepair.repair(await res.arrayBuffer());
  debug.repairedBufferViews = fixed.repaired;
  const gltf = await new GLTFLoader().parseAsync(fixed.buffer, avatarUrl.replace(/[^/]*$/, ""));
  const root = gltf.scene;
  scene.add(root);
  root.updateMatrixWorld(true);
  const status = window.PubcastGLBMotionConsumer.registerAvatar(avatarId, root, {});
  debug.mapped = status.mappedBones;
  debug.loaded = true;
}

let polling = false;
async function tick() {
  if (polling) return;
  polling = true;
  try {
    const res = await fetch(`/api/camera-view/state/${encodeURIComponent(cameraId)}?avatar=${encodeURIComponent(avatarId)}`, { credentials: "same-origin", cache: "no-store" });
    if (!res.ok) throw new Error(`camera state ${res.status}`);
    const state = await res.json();
    applyPose(state.pose);
    if (debug.loaded) {
      const result = window.PubcastGLBMotionConsumer.consumePayload({ avatar_id: avatarId, commands: [state.motion] });
      if (result.ok) debug.applied += result.applied;
      else debug.error = result.error || "motion not applied";
    }
    debug.lastMotion = { source: state.motion.source, t: state.server_time };
    debug.ticks += 1;
  } catch (err) {
    debug.error = String(err && err.message || err);
  } finally {
    polling = false;
  }
}

const session = "camview" + Math.random().toString(36).slice(2, 12);
let publishing = false, lastPublishAt = 0;
async function publishFrame() {
  if (!publish || publishing || !debug.loaded || !debug.ticks) return;
  publishing = true;
  try {
    const blob = await new Promise((resolve, reject) => canvas.toBlob(b => b && b.size ? resolve(b) : reject(new Error("no JPEG data")), "image/jpeg", 0.8));
    const res = await fetch(`/api/studio25d/media/frames/${encodeURIComponent(cameraId)}`, {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "image/jpeg", "X-Studio-Session": session }, body: blob,
    });
    if (!res.ok) throw new Error(`frame publish ${res.status}: ${(await res.text()).slice(0, 120)}`);
    const body = await res.json();
    debug.published += 1; debug.lastPublish = body;
  } catch (err) {
    debug.publishErrors += 1; debug.error = String(err && err.message || err);
  } finally {
    publishing = false;
  }
}

function frame(now) {
  renderer.render(scene, camera);
  debug.frames += 1;
  if (now - lastPublishAt >= PUBLISH_MS) { lastPublishAt = now; publishFrame(); }
  hud.textContent = `${cameraId}  tick ${debug.ticks}  frame ${debug.frames}${publish ? `  published ${debug.published}` : ""}${debug.error ? `\n${debug.error}` : ""}`;
  requestAnimationFrame(frame);
}

loadAvatar().then(() => { setInterval(tick, POLL_MS); }).catch(err => { debug.error = "avatar load failed: " + (err && err.message || err); });
tick();
requestAnimationFrame(frame);
