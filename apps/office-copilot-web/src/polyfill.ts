const map = Map.prototype as Map<unknown, unknown> & {
  getOrInsert?(key: unknown, value: unknown): unknown;
  getOrInsertComputed?(key: unknown, compute: (key: unknown) => unknown): unknown;
};
if (typeof map.getOrInsert !== 'function') {
  map.getOrInsert = function(key, value) {
    if (!this.has(key)) this.set(key, value);
    return this.get(key);
  };
}
if (typeof map.getOrInsertComputed !== 'function') {
  map.getOrInsertComputed = function(key, compute) {
    if (!this.has(key)) this.set(key, compute(key));
    return this.get(key);
  };
}
