import { useCallback, useEffect, useRef, useState } from 'react';
import { askStream, fetchFilters, fetchStats, forgetConversation } from './lib/api';
import type { ChatTurn, Filters, Stats } from './lib/types';
import { STRINGS, type Lang } from './lib/i18n';
import { Composer, DEFAULT_K, type QueryState } from './components/Composer';
import { ChatView } from './components/ChatView';
import { StatsGauge } from './components/StatsGauge';
import { LangToggle } from './components/LangToggle';
import { VaporField } from './components/VaporField';
import { IngestTab } from './components/IngestTab';
import styles from './App.module.css';

type Tab = 'consultar' | 'adicionar';

function readHashTab(): Tab {
  return window.location.hash === '#/adicionar' ? 'adicionar' : 'consultar';
}

const INITIAL_QUERY: QueryState = {
  query: '',
  ingredients: [],
  kind: '',
  region: '',
  dishType: '',
  k: DEFAULT_K,
};

function readStoredLang(): Lang {
  try {
    const v = localStorage.getItem('chef-library-lang');
    return v === 'en' ? 'en' : 'pt';
  } catch {
    return 'pt';
  }
}

function App() {
  const [lang, setLang] = useState<Lang>(readStoredLang);
  const [stats, setStats] = useState<Stats | null>(null);
  const [filters, setFilters] = useState<Filters | null>(null);
  const [indexReady, setIndexReady] = useState<boolean | null>(null);
  const [tab, setTab] = useState<Tab>(readHashTab);

  const [queryState, setQueryState] = useState<QueryState>(INITIAL_QUERY);
  // a conversa vive só na página (e na memória curta do agente, também efêmera)
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [conversationId, setConversationId] = useState<string>(() => crypto.randomUUID());
  const cancelRef = useRef<() => void>(() => {});

  useEffect(() => {
    const onHash = () => setTab(readHashTab());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const loadLibrary = useCallback(() => {
    fetchStats()
      .then((s) => {
        setStats(s);
        setIndexReady(true);
      })
      .catch(() => setIndexReady(false));
    fetchFilters()
      .then(setFilters)
      .catch(() => {});
  }, []);

  useEffect(loadLibrary, [loadLibrary]);

  useEffect(() => {
    document.title = STRINGS[lang].wordmarkTitle;
  }, [lang]);

  const handleLangChange = useCallback((next: Lang) => {
    setLang(next);
    try {
      localStorage.setItem('chef-library-lang', next);
    } catch {
      /* conveniência por visitante — tudo bem se o storage não existir */
    }
  }, []);

  const busy = turns.some((x) => x.phase === 'loading' || x.phase === 'streaming');

  const ask = useCallback(
    (question: string) => {
      const q = question.trim();
      if (!q) return;
      cancelRef.current();
      const id = crypto.randomUUID();
      const update = (patch: (turn: ChatTurn) => Partial<ChatTurn>) =>
        setTurns((prev) => prev.map((x) => (x.id === id ? { ...x, ...patch(x) } : x)));

      setTurns((prev) => [
        ...prev,
        { id, question: q, answer: '', hits: [], webSources: [], grounded: true, phase: 'loading', searching: null, error: null },
      ]);
      setQueryState((prev) => ({ ...prev, query: '' }));

      cancelRef.current = askStream(
        {
          q,
          k: queryState.k,
          lang,
          kind: queryState.kind || undefined,
          ingredient: queryState.ingredients.length ? queryState.ingredients.join(',') : undefined,
          region: queryState.region || undefined,
          dish_type: queryState.dishType || undefined,
          conversation_id: conversationId,
        },
        {
          onSources: (hits) => update(() => ({ hits, grounded: true, phase: 'streaming' })),
          onFallback: () => update(() => ({ hits: [], grounded: false, phase: 'streaming' })),
          onSearching: (query) => update(() => ({ searching: query })),
          onWebSources: (sources) => update((x) => ({ searching: null, webSources: [...x.webSources, ...sources] })),
          onToken: (token) => update((x) => ({ answer: x.answer + token })),
          onError: (message) => update(() => ({ phase: 'error', error: message, searching: null })),
          onDone: () => update(() => ({ phase: 'done', searching: null })),
          connectionLostMessage: STRINGS[lang].connectionLost,
        },
      );
    },
    [queryState, lang, conversationId],
  );

  const handleNewConversation = useCallback(() => {
    cancelRef.current();
    forgetConversation(conversationId).catch(() => {});
    setConversationId(crypto.randomUUID());
    setTurns([]);
    setQueryState((prev) => ({ ...prev, query: '' }));
  }, [conversationId]);

  useEffect(() => () => cancelRef.current(), []);

  const t = STRINGS[lang];

  return (
    <div className={styles.app}>
      <VaporField />
      <header className={styles.topbar}>
        <div className={styles.brand}>
          <span className={styles.wordmark}>{t.wordmarkTitle}</span>
          <StatsGauge stats={stats} lang={lang} />
        </div>
        <nav className={styles.tabs} aria-label={t.tabsAriaLabel}>
          <a
            href="#/consultar"
            className={`${styles.tab}${tab === 'consultar' ? ` ${styles.tabActive}` : ''}`}
            aria-current={tab === 'consultar' ? 'page' : undefined}
          >
            {t.tabSearch}
          </a>
          <a
            href="#/adicionar"
            className={`${styles.tab}${tab === 'adicionar' ? ` ${styles.tabActive}` : ''}`}
            aria-current={tab === 'adicionar' ? 'page' : undefined}
          >
            {t.tabIngest}
          </a>
        </nav>
        <div className={styles.topRight}>
          <LangToggle lang={lang} onChange={handleLangChange} />
        </div>
      </header>

      {/* as duas abas ficam montadas: trocar de aba não perde uma resposta em
          andamento, e a fila de ingestão continua atualizando o medidor */}
      <main className={styles.main}>
        <div className={styles.pane} hidden={tab !== 'consultar'}>
          <ChatView
            turns={turns}
            indexReady={indexReady}
            onExample={ask}
            lang={lang}
            composer={
              <Composer
                state={queryState}
                onChange={setQueryState}
                onSubmit={() => ask(queryState.query)}
                filters={filters}
                busy={busy}
                turns={turns.length}
                onNewConversation={handleNewConversation}
                lang={lang}
              />
            }
          />
        </div>
        <div className={`${styles.pane} ${styles.ingestPane}`} hidden={tab !== 'adicionar'}>
          <div className={styles.ingestInner}>
            <IngestTab lang={lang} onLibraryChanged={loadLibrary} />
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
