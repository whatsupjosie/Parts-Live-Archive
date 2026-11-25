"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { repair } = require("../static/glb_buffer_repair.js");

function chunks(ab) {
  const v = new DataView(ab), out = {};
  let off = 12;
  while (off < ab.byteLength) {
    const len = v.getUint32(off, true), type = v.getUint32(off + 4, true);
    out[type] = { off: off + 8, len };
    off += 8 + len;
  }
  return out;
}
function jsonOf(ab) {
  const c = chunks(ab)[0x4e4f534a];
  return JSON.parse(new TextDecoder().decode(new Uint8Array(ab, c.off, c.len)));
}
function toAB(buf) { return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength); }

const root = path.join(__dirname, "..");
for (const name of ["manny.glb", "sheila.glb"]) {
  const original = toAB(fs.readFileSync(path.join(root, "assets", "avatar", name)));
  const before = jsonOf(original);
  assert.ok(before.bufferViews.every(v => v.buffer === undefined), name + " should still have the defect on disk");
  const result = repair(original);
  assert.strictEqual(result.repaired, before.bufferViews.length, name);
  const fixed = result.buffer;
  assert.strictEqual(new DataView(fixed).getUint32(8, true), fixed.byteLength, "total length in header");
  assert.strictEqual(fixed.byteLength % 4, 0);
  const after = jsonOf(fixed);
  assert.ok(after.bufferViews.every(v => v.buffer === 0), name);
  assert.deepStrictEqual(after.accessors, before.accessors);
  assert.deepStrictEqual(after.nodes, before.nodes);
  const binBefore = chunks(original)[0x004e4942], binAfter = chunks(fixed)[0x004e4942];
  assert.ok(Buffer.from(original, binBefore.off, binBefore.len).equals(Buffer.from(fixed, binAfter.off, binAfter.len)), "binary data identical");
  assert.strictEqual(repair(fixed).buffer, fixed, "a repaired file is left alone the second time");
  assert.strictEqual(repair(fixed).repaired, 0);
}

const pete = toAB(fs.readFileSync(path.join(root, "assets", "pete_avatar_pubcast_v56.glb")));
const same = repair(pete);
assert.strictEqual(same.buffer, pete, "a valid file comes back as the same object, byte-for-byte");
assert.strictEqual(same.repaired, 0);

const junk = new ArrayBuffer(64);
assert.strictEqual(repair(junk).buffer, junk);
assert.strictEqual(repair("not a buffer").repaired, 0);
assert.strictEqual(repair(new ArrayBuffer(4)).repaired, 0);

// several buffers: the missing index could mean any of them, so nothing is guessed
function build(doc) {
  const enc = new TextEncoder().encode(JSON.stringify(doc));
  const pad = new Uint8Array(Math.ceil(enc.length / 4) * 4).fill(0x20); pad.set(enc);
  const out = new Uint8Array(20 + pad.length);
  const v = new DataView(out.buffer);
  v.setUint32(0, 0x46546c67, true); v.setUint32(4, 2, true); v.setUint32(8, out.length, true);
  v.setUint32(12, pad.length, true); v.setUint32(16, 0x4e4f534a, true); out.set(pad, 20);
  return out.buffer;
}
const ambiguous = build({ asset: { version: "2.0" }, buffers: [{ byteLength: 4 }, { byteLength: 4 }], bufferViews: [{ byteLength: 4 }] });
assert.strictEqual(repair(ambiguous).buffer, ambiguous);
assert.strictEqual(repair(ambiguous).repaired, 0);

console.log("glb_buffer_repair: ok");
