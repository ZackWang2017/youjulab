import { remoteJson } from '../remote/client';
import { office } from '../office-client/client';

export type InformationKind = 'wechat' | 'finance_api' | 'finance_rss' | 'holdings_announce' | 'favorites';
export type InformationView = 'today' | 'review';

export interface InformationGroup {
  id: number;
  name: string;
  kind: InformationKind;
  config: InformationConfig;
  created_at: string;
  updated_at: string;
}

export interface InformationConfig {
  adapters?: string[];
  keywords?: string;
  holdings?: HoldingPick[];
}

export interface HoldingPick {
  ticker_code: string;
  name: string;
  market_code: string;
  adapter: string;
  adapters?: Adapter[];
}

export interface Adapter {
  id: string;
  name: string;
  site?: string;
  icon?: string;
  markets?: string[];
}

export interface InformationItem {
  id: number;
  group_id: number;
  source_kind: InformationKind | string;
  source_name: string;
  source_icon: string;
  title: string;
  url: string;
  published_at: string;
  fetched_at: string;
  fetched_date: string;
  summary: string;
  impact: string;
  favorited: boolean;
  hidden: boolean;
  favorite_from_kind: string;
  favorite_from_name: string;
  analysis: { summary: string; performance: string; financial: string } | null;
}

export interface InformationReview {
  id: number;
  group_id: number;
  review_date: string;
  success_count: number;
  fail_count: number;
  sentiment: string;
  content: string;
  item_refs: ReviewItemRef[];
  created_at: string;
}

export interface ReviewItemRef {
  id: number;
  title: string;
  url: string;
  sentiment: string;
}

export interface TodaySummary {
  group: InformationGroup;
  date: string;
  success_count: number;
  fail_count: number;
  sentiment: string;
  item_count: number;
}

export interface FetchResult {
  group: InformationGroup;
  inserted: InformationItem[];
  skipped: number;
  failed: string[];
  login_required: boolean;
}

export interface WereadSession {
  status: string;
  session_id: string;
  qr_data_url: string;
  error: string;
}

const base = '/api/information';

export const KIND_LABEL: Record<InformationKind, string> = {
  wechat: '公众号',
  finance_api: '财经 API',
  finance_rss: '财经 RSS',
  holdings_announce: '持仓公告',
  favorites: '收藏夹',
};

export const informationApi = {
  list: () => remoteJson<{ groups: InformationGroup[] }>(`${base}/groups`).then(r => r.groups),
  create: (body: { name: string; kind: InformationKind; config?: InformationConfig }) =>
    remoteJson<InformationGroup>(`${base}/groups`, { method: 'POST', body: JSON.stringify(body) }),
  update: (id: number, body: { name?: string; config?: InformationConfig }) =>
    remoteJson<InformationGroup>(`${base}/groups/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  remove: (id: number) => remoteJson<{ ok: boolean }>(`${base}/groups/${id}`, { method: 'DELETE' }),
  items: (id: number, today = true, opts?: { review?: boolean }) =>
    remoteJson<{ group: InformationGroup; items: InformationItem[] }>(
      `${base}/groups/${id}/items?today=${today ? 1 : 0}${opts?.review ? '&review=1' : ''}`,
    ),
  fetchToday: (id: number) =>
    remoteJson<FetchResult>(`${base}/groups/${id}/fetch`, { method: 'POST' }),
  summary: (id: number) => remoteJson<TodaySummary>(`${base}/groups/${id}/summary`),
  reviews: (id: number) =>
    remoteJson<{ group: InformationGroup; reviews: InformationReview[] }>(`${base}/groups/${id}/reviews`).then(r => r.reviews),
  review: (id: number) => remoteJson<InformationReview>(`${base}/reviews/${id}`),
  clear: () => remoteJson<{ deleted: number }>(`${base}/clear`, { method: 'POST' }),
  favorite: (id: number, favorited = true) =>
    remoteJson<InformationItem>(`${base}/items/${id}/favorite`, { method: 'POST', body: JSON.stringify({ favorited }) }),
  hide: (id: number) => remoteJson<InformationItem>(`${base}/items/${id}`, { method: 'DELETE' }),
  adapters: (kind: string) => remoteJson<{ adapters: Adapter[] }>(`${base}/adapters?kind=${encodeURIComponent(kind)}`).then(r => r.adapters),
  holdings: () => remoteJson<{ holdings: HoldingPick[] }>(`${base}/holdings`).then(r => r.holdings),
  wereadStatus: () => remoteJson<WereadSession>(`${base}/weread/status`),
  wereadLogin: () => remoteJson<WereadSession>(`${base}/weread/login`, { method: 'POST' }),
  wereadLoginStatus: (sessionId: string) => remoteJson<WereadSession>(`${base}/weread/login/${encodeURIComponent(sessionId)}`),
};

export const informationSkills = {
  keywords: (body: { kind: string; description: string; extra?: Record<string, unknown> }, onEvent: (event: Record<string, any>) => void) =>
    office.stream('/assistant/information/keywords', body, onEvent),
  impact: (body: { group_id: number; item_ids?: number[] }, onEvent: (event: Record<string, any>) => void) =>
    office.stream('/assistant/information/impact', body, onEvent),
  analyze: (body: { item_id: number }, onEvent: (event: Record<string, any>) => void) =>
    office.stream('/assistant/information/analyze', body, onEvent),
  review: (body: { group_id: number }, onEvent: (event: Record<string, any>) => void) =>
    office.stream('/assistant/information/review', body, onEvent),
};
