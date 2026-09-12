/*
 * microguard fingerprint probe.
 *
 * Collects a handful of environment signals, hashes them IN THE BROWSER, and
 * sends only the hash. The server never receives a raw component, so it cannot
 * reconstruct what was measured. That is a deliberate limit on this tool, not
 * an incidental one -- see the privacy note in the README.
 *
 * What this does and does not do: it raises the bar. A plain HTTP client, a
 * simple script, or a naive scraper never runs this at all, and that is the
 * population it is built to catch. A determined operator running real headless
 * Chrome will produce a perfectly good fingerprint. This is not an arms-race
 * winner and nothing derived from it should claim to be.
 *
 * Every probe is individually guarded. A browser with canvas blocked, WebGL
 * disabled, or a privacy extension installed must still produce a hash and
 * still reach the endpoint -- otherwise the absence rule would flag exactly
 * the privacy-conscious humans it should leave alone.
 */
(function () {
  'use strict';

  var ENDPOINT = '/microguard/fp';

  function safe(fn, fallback) {
    try {
      var value = fn();
      return value === undefined || value === null ? fallback : value;
    } catch (e) {
      return fallback;
    }
  }

  function canvasFingerprint() {
    var canvas = document.createElement('canvas');
    canvas.width = 240;
    canvas.height = 60;
    var ctx = canvas.getContext('2d');
    if (!ctx) return 'no-canvas';
    // Fixed drawing operations. The variation comes from the renderer, the
    // font stack and antialiasing, not from anything chosen here.
    ctx.textBaseline = 'top';
    ctx.font = '14px "Arial"';
    ctx.fillStyle = '#f60';
    ctx.fillRect(10, 1, 62, 20);
    ctx.fillStyle = '#069';
    ctx.fillText('microguard ✓ éåø', 2, 15);
    ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
    ctx.fillText('microguard ✓ éåø', 4, 25);
    ctx.globalCompositeOperation = 'multiply';
    ctx.beginPath();
    ctx.arc(50, 30, 20, 0, Math.PI * 2, true);
    ctx.fill();
    return canvas.toDataURL();
  }

  function webglFingerprint() {
    var canvas = document.createElement('canvas');
    var gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (!gl) return 'no-webgl';
    var info = gl.getExtension('WEBGL_debug_renderer_info');
    if (!info) return String(gl.getParameter(gl.VERSION));
    return [
      gl.getParameter(info.UNMASKED_VENDOR_WEBGL),
      gl.getParameter(info.UNMASKED_RENDERER_WEBGL)
    ].join('|');
  }

  /*
   * Font detection by width probing: render the same string in a candidate
   * font with a known fallback, and compare widths. A font that is absent
   * falls back and measures identically; one that is present does not.
   */
  function fontFingerprint() {
    var canvas = document.createElement('canvas');
    var ctx = canvas.getContext('2d');
    if (!ctx) return 'no-canvas';
    var probe = 'mmmmmmmmmmlli';
    var bases = ['monospace', 'sans-serif', 'serif'];
    var candidates = [
      'Arial', 'Courier New', 'Georgia', 'Helvetica Neue', 'Times New Roman',
      'Trebuchet MS', 'Verdana', 'Segoe UI', 'Roboto', 'Ubuntu',
      'Menlo', 'Consolas', 'DejaVu Sans', 'Liberation Sans', 'Noto Sans'
    ];
    var baseline = {};
    bases.forEach(function (base) {
      ctx.font = '72px ' + base;
      baseline[base] = ctx.measureText(probe).width;
    });
    var present = [];
    candidates.forEach(function (name) {
      var found = bases.some(function (base) {
        ctx.font = '72px "' + name + '",' + base;
        return ctx.measureText(probe).width !== baseline[base];
      });
      if (found) present.push(name);
    });
    return present.join(',');
  }

  function collect() {
    return [
      safe(canvasFingerprint, 'canvas-error'),
      safe(webglFingerprint, 'webgl-error'),
      safe(fontFingerprint, 'font-error'),
      safe(function () { return navigator.hardwareConcurrency; }, 0),
      safe(function () { return navigator.language; }, ''),
      safe(function () { return screen.width + 'x' + screen.height; }, ''),
      safe(function () { return screen.colorDepth; }, 0),
      safe(function () { return new Date().getTimezoneOffset(); }, 0),
      safe(function () {
        return Intl.DateTimeFormat().resolvedOptions().timeZone;
      }, '')
    ].join('~~');
  }

  function toHex(buffer) {
    var bytes = new Uint8Array(buffer);
    var out = '';
    for (var i = 0; i < bytes.length; i++) {
      out += bytes[i].toString(16).padStart(2, '0');
    }
    return out;
  }

  function send(hash) {
    // keepalive so the POST survives a fast navigation away. A fingerprint
    // lost to a quick click would read as an absent one, which is evidence.
    fetch(ENDPOINT, {
      method: 'POST',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fingerprint_hash: hash })
    }).catch(function () {
      // The site is not ours to break. A failed submission means one missing
      // signal, and the server side is built to treat that as unknown.
    });
  }

  function run() {
    /*
     * SubtleCrypto only exists in a secure context: HTTPS, or localhost. On a
     * plain-HTTP origin window.crypto.subtle is undefined and this script can
     * do nothing at all. That is a browser rule, not a choice made here.
     *
     * It matters operationally because the absence rule reads a missing
     * fingerprint as evidence. A site served over HTTP would produce zero
     * fingerprints and look like a site full of bots, so say why out loud
     * rather than returning quietly -- "no fingerprints ever arrive" is
     * otherwise unexplainable from the outside.
     */
    if (!window.crypto || !window.crypto.subtle) {
      if (window.console && console.warn) {
        console.warn(
          'microguard: fingerprinting needs a secure context (HTTPS or ' +
          'localhost). No fingerprint will be sent from this origin.'
        );
      }
      return;
    }
    if (!window.fetch) return;
    var encoded = new TextEncoder().encode(collect());
    window.crypto.subtle
      .digest('SHA-256', encoded)
      .then(function (buffer) { send(toHex(buffer)); })
      .catch(function () { /* no hash, no submission */ });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run);
  } else {
    run();
  }
})();
