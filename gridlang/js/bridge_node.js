// gridlang/js/bridge_node.js — Node-side bridge for the in-process JS runtime.
//
// Reads a request JSON from stdin, runs user compute code in a vm sandbox,
// writes a response JSON to stdout. Wire format documented in
// gridlang/js_runtime.py.
//
// Placeholder __HELPERS_PRELUDE__ is replaced at runtime with the contents
// of df_helpers.js, JSON-encoded.

const vm = require('vm');

function readAllStdin() {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', c => { data += c; });
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

function emit(payload) {
  process.stdout.write(JSON.stringify(payload));
}

(async () => {
  try {
    const raw = await readAllStdin();
    const req = JSON.parse(raw);
    const userCode = String(req.code || '');
    const isMulti = !!req.is_multi_sheet;
    const timeoutMs = Number(req.timeout_ms) || 5000;

    // Build the sandboxed context. Deliberately spartan globals.
    const sandbox = {
      console: { log: () => {}, warn: () => {}, error: () => {} },
      Math, JSON, Number, String, Boolean, Array, Object, Date,
      isFinite, isNaN, parseFloat, parseInt,
      Map, Set, WeakMap, WeakSet, Symbol,
      Error, TypeError, RangeError, SyntaxError,
      Promise,
    };
    sandbox.globalThis = sandbox;
    const ctx = vm.createContext(sandbox, { name: 'gridlang-js' });

    // Run helpers prelude.
    vm.runInContext(__HELPERS_PRELUDE__, ctx, { timeout: 1000 });

    // Run user code so it defines transform/aggregates/etc on the sandbox globals.
    try {
      vm.runInContext(userCode, ctx, { timeout: timeoutMs, filename: 'compute.js' });
    } catch (e) {
      emit({ error: 'Error loading compute section: ' + (e && e.message || String(e)) });
      return;
    }

    const found = [];
    for (const fn of ['validate', 'transform', 'aggregates', 'conditional_formats']) {
      if (typeof sandbox[fn] === 'function') found.push(fn);
    }

    // Step 1: validate
    let validationMessages = [];
    if (typeof sandbox.validate === 'function') {
      try {
        const callExpr = `validate(makeDF(${JSON.stringify(req.df || [])}))`;
        const messages = vm.runInContext(callExpr, ctx, { timeout: timeoutMs });
        if (messages && messages.length) {
          validationMessages = Array.isArray(messages) ? messages : [String(messages)];
          emit({
            error: 'Validation failed:\n' + validationMessages.map(m => '  - ' + m).join('\n'),
            validation_messages: validationMessages,
          });
          return;
        }
      } catch (e) {
        emit({ error: 'Error in validate(): ' + (e && e.message || String(e)) });
        return;
      }
    }

    // Step 2: transform
    let resultDf = req.df || [];
    let resultSheets = req.sheets || { default: req.df || [] };

    if (typeof sandbox.transform === 'function') {
      // Detect whether the function expects sheets or a single df by parameter name.
      const fnSrc = sandbox.transform.toString();
      const paramMatch = fnSrc.match(/function[^(]*\(([^)]*)\)|\(([^)]*)\)\s*=>/);
      const firstParam = ((paramMatch && (paramMatch[1] || paramMatch[2])) || '').split(',')[0].trim();
      const wantsSheets = isMulti && (firstParam === 'sheets' || firstParam === 'dfs');

      try {
        if (wantsSheets) {
          const sheetsLiteral = JSON.stringify(req.sheets || {});
          const wrap = `(function(){ const sheets = {}; const __raw = ${sheetsLiteral};
            for (const k in __raw) sheets[k] = makeDF(__raw[k].slice());
            const out = transform(sheets);
            if (out === undefined) throw new Error("transform() returned undefined; did you forget to return sheets?");
            const flat = {};
            for (const k in out) flat[k] = Array.from(out[k] || []);
            return flat;
          })()`;
          const out = vm.runInContext(wrap, ctx, { timeout: timeoutMs });
          resultSheets = out;
          const firstKey = Object.keys(out)[0];
          resultDf = firstKey ? out[firstKey] : [];
        } else {
          const wrap = `(function(){ const df = makeDF(${JSON.stringify(req.df || [])});
            const out = transform(df);
            if (out === undefined) throw new Error("transform() returned undefined; did you forget to return df?");
            return Array.from(out);
          })()`;
          const out = vm.runInContext(wrap, ctx, { timeout: timeoutMs });
          resultDf = out;
          resultSheets = { default: out };
        }
      } catch (e) {
        emit({ error: 'Error in transform(): ' + (e && e.message || String(e)) });
        return;
      }
    }

    // Step 3: aggregates
    let agg = {};
    if (typeof sandbox.aggregates === 'function') {
      try {
        const wrap = `aggregates(makeDF(${JSON.stringify(resultDf)}))`;
        const out = vm.runInContext(wrap, ctx, { timeout: timeoutMs });
        if (out !== null && out !== undefined) {
          if (typeof out !== 'object' || Array.isArray(out)) {
            emit({ error: 'aggregates() must return an object, got ' + typeof out });
            return;
          }
          agg = out;
        }
      } catch (e) {
        emit({ error: 'Error in aggregates(): ' + (e && e.message || String(e)) });
        return;
      }
    }

    // Step 4: conditional_formats
    let condFormats = [];
    if (typeof sandbox.conditional_formats === 'function') {
      try {
        const out = vm.runInContext('conditional_formats()', ctx, { timeout: timeoutMs });
        if (Array.isArray(out)) {
          condFormats = out.filter(r => r && typeof r === 'object');
        }
      } catch (e) {
        emit({ error: 'Error in conditional_formats(): ' + (e && e.message || String(e)) });
        return;
      }
    }

    emit({
      df: resultDf,
      sheets: resultSheets,
      aggregates: agg,
      conditional_formats: condFormats,
      validation_messages: validationMessages,
      found_functions: found,
      error: null,
    });
  } catch (outer) {
    emit({ error: 'Bridge crashed: ' + (outer && outer.message || String(outer)) });
  }
})();
