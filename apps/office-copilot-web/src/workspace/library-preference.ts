const sessionPreferences = new Map<string, boolean>();
const preferenceKey = (userId: string) => `youjulab:library-expanded:${userId}`;

export function libraryExpanded(userId: string): boolean {
  try {
    const saved = localStorage.getItem(preferenceKey(userId));
    if (saved === 'true' || saved === 'false') return saved === 'true';
  } catch { /* Storage can be unavailable in private/restricted environments. */ }
  return sessionPreferences.get(userId) ?? true;
}

export function saveLibraryExpanded(userId: string, expanded: boolean) {
  sessionPreferences.set(userId, expanded);
  try { localStorage.setItem(preferenceKey(userId), String(expanded)); }
  catch { /* Keep this session usable even if the preference cannot be persisted. */ }
}
