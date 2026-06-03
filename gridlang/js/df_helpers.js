// gridlang/js/df_helpers.js — Helpers attached to the array-of-records df.
//
// Distributed both as the in-process bridge prelude (gridlang.js_runtime)
// and embedded in Node/Browser bundles produced by `gridlang js-bundle`.
//
// All methods are non-enumerable so JSON.stringify(df) stays clean.
// Numeric methods coerce values with the unary + and skip NaN; missing
// columns return [], NaN, or null as appropriate.

function makeDF(records) {
  // df IS the array; we just decorate it.
  const df = records;

  function def(name, value, opts) {
    Object.defineProperty(df, name, Object.assign({ value, enumerable: false, writable: true, configurable: true }, opts || {}));
  }
  function getter(name, fn) {
    Object.defineProperty(df, name, { get: fn, enumerable: false, configurable: true });
  }

  // ─── Column / row access ──────────────────────────────────────────────
  def('col', function(name) {
    return this.map(r => r[name]);
  });
  def('row', function(i) { return this[i]; });
  def('pluck', function(...names) {
    return this.map(r => {
      const o = {};
      for (const n of names) o[n] = r[n];
      return o;
    });
  });
  def('drop', function(...names) {
    return makeDF(this.map(r => {
      const o = {};
      for (const k of Object.keys(r)) if (!names.includes(k)) o[k] = r[k];
      return o;
    }));
  });
  def('rename', function(map) {
    return makeDF(this.map(r => {
      const o = {};
      for (const k of Object.keys(r)) o[map[k] || k] = r[k];
      return o;
    }));
  });

  // ─── Aggregations ─────────────────────────────────────────────────────
  def('count', function() { return this.length; });
  def('sum', function(name) {
    let s = 0;
    for (const r of this) { const v = +r[name]; if (!Number.isNaN(v)) s += v; }
    return s;
  });
  def('mean', function(name) {
    if (!this.length) return NaN;
    return this.sum(name) / this.length;
  });
  def('max', function(name) {
    let m = -Infinity;
    for (const r of this) { const v = +r[name]; if (v > m) m = v; }
    return m === -Infinity ? null : m;
  });
  def('min', function(name) {
    let m = Infinity;
    for (const r of this) { const v = +r[name]; if (v < m) m = v; }
    return m === Infinity ? null : m;
  });
  def('variance', function(name) {
    const n = this.length;
    if (n < 2) return NaN;
    const mu = this.mean(name);
    let s = 0;
    for (const r of this) { const v = +r[name]; if (!Number.isNaN(v)) s += (v - mu) * (v - mu); }
    return s / (n - 1);  // sample variance
  });
  def('std', function(name) { return Math.sqrt(this.variance(name)); });
  def('median', function(name) {
    return this.quantile(name, 0.5);
  });
  def('quantile', function(name, q) {
    const arr = this.col(name).map(Number).filter(v => !Number.isNaN(v)).sort((a, b) => a - b);
    if (!arr.length) return NaN;
    const idx = (arr.length - 1) * q;
    const lo = Math.floor(idx), hi = Math.ceil(idx);
    if (lo === hi) return arr[lo];
    return arr[lo] + (arr[hi] - arr[lo]) * (idx - lo);
  });
  def('describe', function() {
    const cols = this.columns;
    const out = {};
    for (const c of cols) {
      const arr = this.col(c).map(Number).filter(v => !Number.isNaN(v));
      if (!arr.length) { out[c] = null; continue; }
      out[c] = {
        count: arr.length,
        mean: this.mean(c),
        std:  this.std(c),
        min:  this.min(c),
        q25:  this.quantile(c, 0.25),
        q50:  this.quantile(c, 0.50),
        q75:  this.quantile(c, 0.75),
        max:  this.max(c),
      };
    }
    return out;
  });

  // ─── Filtering / slicing ──────────────────────────────────────────────
  def('where', function(pred) { return makeDF(this.filter(pred)); });
  def('head', function(n) { n = (n == null) ? 5 : n; return makeDF(this.slice(0, n)); });
  def('tail', function(n) { n = (n == null) ? 5 : n; return makeDF(this.slice(this.length - n)); });
  def('slice', function(start, end) { return makeDF(Array.prototype.slice.call(this, start, end)); });
  def('distinct', function(name) {
    if (name == null) {
      const seen = new Set(), out = [];
      for (const r of this) {
        const k = JSON.stringify(r);
        if (!seen.has(k)) { seen.add(k); out.push(r); }
      }
      return makeDF(out);
    }
    const seen = new Set(), out = [];
    for (const r of this) {
      const k = r[name];
      if (!seen.has(k)) { seen.add(k); out.push(r); }
    }
    return makeDF(out);
  });
  def('find', function(pred) { return Array.prototype.find.call(this, pred); });
  def('some', function(pred) { return Array.prototype.some.call(this, pred); });
  def('every', function(pred) { return Array.prototype.every.call(this, pred); });
  def('none', function(pred) { return !Array.prototype.some.call(this, pred); });

  // ─── Sorting / grouping ───────────────────────────────────────────────
  def('sortBy', function(name, opts) {
    opts = opts || {};
    const desc = !!opts.desc;
    const arr = this.slice();
    arr.sort((a, b) => {
      const av = a[name], bv = b[name];
      const an = +av, bn = +bv;
      let cmp;
      if (!Number.isNaN(an) && !Number.isNaN(bn)) cmp = an - bn;
      else cmp = (av > bv) ? 1 : (av < bv ? -1 : 0);
      return desc ? -cmp : cmp;
    });
    return makeDF(arr);
  });
  def('groupBy', function(name) {
    const groups = {};
    for (const r of this) {
      const k = r[name];
      (groups[k] = groups[k] || []).push(r);
    }
    const out = {};
    for (const k of Object.keys(groups)) out[k] = makeDF(groups[k]);
    return out;
  });
  def('countBy', function(name) {
    const out = {};
    for (const r of this) { const k = r[name]; out[k] = (out[k] || 0) + 1; }
    return out;
  });

  // ─── Mutations ────────────────────────────────────────────────────────
  def('addColumn', function(name, fn) {
    for (let i = 0; i < this.length; i++) {
      const r = this[i];
      r[name] = (typeof fn === 'function') ? fn(r, i) : fn;
    }
    return this;
  });
  def('assign', function(obj) {
    // Add multiple columns at once: df.assign({Tax: r => r.Revenue * 0.2, Net: 100})
    for (const name of Object.keys(obj)) this.addColumn(name, obj[name]);
    return this;
  });

  // ─── Joins ────────────────────────────────────────────────────────────
  def('join', function(right, key, opts) {
    // Inner join on a shared key. If `right` is an array-of-records, build an index.
    opts = opts || {};
    const rightKey = opts.rightKey || key;
    const rightIdx = {};
    for (const r of right) rightIdx[r[rightKey]] = r;
    const out = [];
    for (const l of this) {
      const r = rightIdx[l[key]];
      if (r) {
        const merged = Object.assign({}, l);
        for (const k of Object.keys(r)) if (k !== rightKey) merged[k] = r[k];
        out.push(merged);
      }
    }
    return makeDF(out);
  });
  def('leftJoin', function(right, key, opts) {
    opts = opts || {};
    const rightKey = opts.rightKey || key;
    const rightIdx = {};
    for (const r of right) rightIdx[r[rightKey]] = r;
    const out = [];
    for (const l of this) {
      const r = rightIdx[l[key]];
      const merged = Object.assign({}, l);
      if (r) for (const k of Object.keys(r)) if (k !== rightKey) merged[k] = r[k];
      out.push(merged);
    }
    return makeDF(out);
  });
  def('concat', function(other) {
    return makeDF(this.concat ? Array.prototype.concat.call(this, other) : [...this, ...other]);
  });

  // ─── Conversion ───────────────────────────────────────────────────────
  def('toRecords', function() { return Array.from(this); });
  def('toCSV', function() {
    if (!this.length) return '';
    const cols = this.columns;
    const escape = v => {
      if (v == null) return '';
      const s = String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    const lines = [cols.join(',')];
    for (const r of this) lines.push(cols.map(c => escape(r[c])).join(','));
    return lines.join('\n');
  });

  // ─── Metadata ────────────────────────────────────────────────────────
  getter('columns', function() { return this.length ? Object.keys(this[0]) : []; });
  getter('shape',   function() { return [this.length, this.columns.length]; });
  getter('empty',   function() { return this.length === 0; });

  return df;
}

// Export hooks for both worlds.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { makeDF };
}
