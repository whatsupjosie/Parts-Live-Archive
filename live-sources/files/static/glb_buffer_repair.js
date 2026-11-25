/* In-memory repair for GLB files whose bufferViews omit the required "buffer" index.
 *
 * assets/avatar/manny.glb and assets/avatar/sheila.glb have all seven bufferViews without "buffer", which the glTF
 * 2.0 spec requires; three.js's GLTFLoader refuses them ("Cannot read properties of undefined (reading 'type')").
 * When the file declares exactly one buffer there is only one thing the index can mean, so it is filled in with 0
 * in memory. The file on disk is never changed, and a GLB that is already valid is returned byte-for-byte as given.
 * Anything this cannot repair with certainty (several buffers, bad header, bad JSON chunk) is returned untouched.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PubCastGlbBufferRepair = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const MAGIC = 0x46546c67, JSON_CHUNK = 0x4e4f534a;

  function repair(input) {
    const unchanged = { buffer: input, repaired: 0 };
    if (!(input instanceof ArrayBuffer) || input.byteLength < 20) return unchanged;
    const view = new DataView(input);
    if (view.getUint32(0, true) !== MAGIC || view.getUint32(4, true) !== 2) return unchanged;
    const jsonLength = view.getUint32(12, true);
    if (view.getUint32(16, true) !== JSON_CHUNK || 20 + jsonLength > input.byteLength) return unchanged;
    let doc;
    try { doc = JSON.parse(new TextDecoder().decode(new Uint8Array(input, 20, jsonLength))); } catch (_) { return unchanged; }
    const views = Array.isArray(doc.bufferViews) ? doc.bufferViews : [];
    const missing = views.filter(v => v && typeof v === "object" && v.buffer === undefined);
    if (!missing.length) return unchanged;
    if (!Array.isArray(doc.buffers) || doc.buffers.length !== 1) return unchanged;
    for (const v of missing) v.buffer = 0;
    const encoded = new TextEncoder().encode(JSON.stringify(doc));
    const padded = new Uint8Array(Math.ceil(encoded.length / 4) * 4).fill(0x20);
    padded.set(encoded);
    const rest = new Uint8Array(input, 20 + jsonLength);
    const out = new Uint8Array(20 + padded.length + rest.length);
    const outView = new DataView(out.buffer);
    out.set(new Uint8Array(input, 0, 12), 0);
    outView.setUint32(8, out.length, true);
    outView.setUint32(12, padded.length, true);
    outView.setUint32(16, JSON_CHUNK, true);
    out.set(padded, 20);
    out.set(rest, 20 + padded.length);
    return { buffer: out.buffer, repaired: missing.length };
  }

  return { repair };
});
