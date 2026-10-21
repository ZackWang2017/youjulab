/** 浏览器本地副本：JSON 资产与 PDF 二进制。服务器仍是多端权威来源。 */

const DB_NAME = 'youjulab-local';
const DB_VERSION = 1;
const JSON_STORE = 'json';
const PDF_STORE = 'pdf';
const MAX_PDF_BYTES = 30 * 1024 * 1024;

export interface LocalJson {
  id: string;
  content: unknown;
  /** 已与服务器对齐的版本；pending 时表示上传所基于的版本。 */
  revision: number;
  baseRevision: number;
  pending: boolean;
  updatedAt: number;
  kind?: string;
  name?: string;
  project_id?: string | null;
}

export interface LocalPdf {
  id: string;
  revision: number;
  bytes: ArrayBuffer;
  updatedAt: number;
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(JSON_STORE)) db.createObjectStore(JSON_STORE, { keyPath: 'id' });
      if (!db.objectStoreNames.contains(PDF_STORE)) {
        const store = db.createObjectStore(PDF_STORE, { keyPath: 'id' });
        store.createIndex('updatedAt', 'updatedAt');
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function txDone(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

export async function getLocalJson(id: string): Promise<LocalJson | null> {
  const db = await openDb();
  const tx = db.transaction(JSON_STORE, 'readonly');
  const row = await new Promise<LocalJson | undefined>((resolve, reject) => {
    const req = tx.objectStore(JSON_STORE).get(id);
    req.onsuccess = () => resolve(req.result as LocalJson | undefined);
    req.onerror = () => reject(req.error);
  });
  return row || null;
}

export async function putLocalJson(row: Omit<LocalJson, 'updatedAt'> & { updatedAt?: number }): Promise<void> {
  const db = await openDb();
  const tx = db.transaction(JSON_STORE, 'readwrite');
  tx.objectStore(JSON_STORE).put({ ...row, updatedAt: row.updatedAt ?? Date.now() });
  await txDone(tx);
}

function looksLikePdf(bytes: ArrayBuffer) {
  if (!bytes || bytes.byteLength < 5) return false;
  const head = new Uint8Array(bytes, 0, 5);
  return head[0] === 0x25 && head[1] === 0x50 && head[2] === 0x44 && head[3] === 0x46 && head[4] === 0x2d; // %PDF-
}

export async function getLocalPdf(id: string, expectedSize?: number | null): Promise<LocalPdf | null> {
  const db = await openDb();
  const tx = db.transaction(PDF_STORE, 'readonly');
  const row = await new Promise<LocalPdf | undefined>((resolve, reject) => {
    const req = tx.objectStore(PDF_STORE).get(id);
    req.onsuccess = () => resolve(req.result as LocalPdf | undefined);
    req.onerror = () => reject(req.error);
  });
  if (!row?.bytes) return null;
  if (!looksLikePdf(row.bytes)) {
    void deleteLocalAsset(id).catch(() => {});
    return null;
  }
  if (expectedSize && expectedSize > 0 && row.bytes.byteLength !== expectedSize) {
    void deleteLocalAsset(id).catch(() => {});
    return null;
  }
  return row;
}

export async function putLocalPdf(id: string, revision: number, bytes: ArrayBuffer): Promise<void> {
  if (bytes.byteLength > MAX_PDF_BYTES) return;
  const db = await openDb();
  const tx = db.transaction(PDF_STORE, 'readwrite');
  tx.objectStore(PDF_STORE).put({ id, revision, bytes, updatedAt: Date.now() } satisfies LocalPdf);
  await txDone(tx);
  await prunePdfs();
}

async function prunePdfs(): Promise<void> {
  const estimate = await navigator.storage?.estimate?.();
  const quota = estimate?.quota || 0;
  const usage = estimate?.usage || 0;
  if (!quota || usage < quota * 0.8) return;
  const db = await openDb();
  const tx = db.transaction(PDF_STORE, 'readwrite');
  const store = tx.objectStore(PDF_STORE);
  const index = store.index('updatedAt');
  const oldest = await new Promise<LocalPdf | undefined>((resolve, reject) => {
    const req = index.openCursor();
    req.onsuccess = () => resolve(req.result?.value as LocalPdf | undefined);
    req.onerror = () => reject(req.error);
  });
  if (oldest) store.delete(oldest.id);
  await txDone(tx);
}

export async function deleteLocalAsset(id: string): Promise<void> {
  const db = await openDb();
  const tx = db.transaction([JSON_STORE, PDF_STORE], 'readwrite');
  tx.objectStore(JSON_STORE).delete(id);
  tx.objectStore(PDF_STORE).delete(id);
  await txDone(tx);
}

export async function clearLocalStore(): Promise<void> {
  const db = await openDb();
  const tx = db.transaction([JSON_STORE, PDF_STORE], 'readwrite');
  tx.objectStore(JSON_STORE).clear();
  tx.objectStore(PDF_STORE).clear();
  await txDone(tx);
}
