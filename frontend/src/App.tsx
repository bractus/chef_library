import { useCallback, useEffect, useRef, useState } from 'react';
import { askStream, fetchFilters, fetchStats } from './lib/api';
import type { Filters, Hit, Stats } from './lib/types';
import { STRINGS, type Lang } from './lib/i18n';
import { FilterPorthole, type QueryState } from './components/FilterPorthole';
import { ResultsArea, type Phase } from './components/ResultsArea';
import { StatsGauge } from './components/StatsGauge';
import { LangToggle } from './components/LangToggle';
import { VaporField } from './components/VaporField';
import styles from './App.module.css';

const INITIAL_QUERY: QueryState = {
  query: '',
  ingredients: [],
  kind: '',
  region: '',
  dishType: '',
  k: 8,
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

  const [queryState, setQueryState] = useState<QueryState>(INITIAL_QUERY);
  const [phase, setPhase] = useState<Phase>('idle');
  const [answer, setAnswer] = useState('');
  const [hits, setHits] = useState<Hit[]>([]);
  const [grounded, setGrounded] = useState(true);
  const [streaming, setStreaming] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [tracedIndex, setTracedIndex] = useState<number | null>(null);

  const cancelRef = useRef<() => void>(() => {});

  useEffect(() => {
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

  useEffect(() => {
    document.title = STRINGS[lang].wordmarkTitle;
  }, [lang]);

  const handleLangChange = useCallback((next: Lang) => {
    setLang(next);
    try {
      localStorage.setItem('chef-library-lang', next);
    } catch {
      /* per-viewer convenience only — fine if storage is unavailable */
    }
  }, []);

  const handleSubmit = useCallback(() => {
    cancelRef.current();
    setPhase('loading');
    setAnswer('');
    setHits([]);
    setGrounded(true);
    setStreaming(false);
    setErrorMessage(null);
    setTracedIndex(null);

    cancelRef.current = askStream(
      {
        q: queryState.query,
        k: queryState.k,
        lang,
        kind: queryState.kind || undefined,
        ingredient: queryState.ingredients.length ? queryState.ingredients.join(',') : undefined,
        region: queryState.region || undefined,
        dish_type: queryState.dishType || undefined,
      },
      {
        onSources: (h) => {
          setHits(h);
          setGrounded(true);
          setPhase('answering');
          setStreaming(true);
        },
        onFallback: () => {
          setHits([]);
          setGrounded(false);
          setPhase('answering');
          setStreaming(true);
        },
        onToken: (t) => setAnswer((prev) => prev + t),
        onError: (msg) => {
          setErrorMessage(msg);
          setPhase('error');
          setStreaming(false);
        },
        onDone: () => setStreaming(false),
        connectionLostMessage: STRINGS[lang].connectionLost,
      },
    );
  }, [queryState, lang]);

  useEffect(() => () => cancelRef.current(), []);

  const t = STRINGS[lang];

  return (
    <div className={styles.page}>
      <VaporField />
      <div className={styles.container}>
        <header className={styles.header}>
          <StatsGauge stats={stats} lang={lang} />
          <div className={styles.headerRight}>
            <span className={styles.wordmarkTitle}>{t.wordmarkTitle}</span>
            <LangToggle lang={lang} onChange={handleLangChange} />
          </div>
        </header>

        {indexReady === false ? (
          <div className={styles.setupPane}>
            <p>
              <strong>{t.setupTitle}</strong>
            </p>
            <p>{t.setupBody1}</p>
            <pre>python -m backend.build_index</pre>
            <p>
              {t.setupBody2} <code>uvicorn backend.api.main:app --reload</code>
            </p>
          </div>
        ) : (
          <FilterPorthole
            state={queryState}
            onChange={setQueryState}
            onSubmit={handleSubmit}
            filters={filters}
            busy={phase === 'loading' || streaming}
            lang={lang}
          />
        )}

        {indexReady !== false && (
          <ResultsArea
            phase={phase}
            answer={answer}
            streaming={streaming}
            hits={hits}
            grounded={grounded}
            errorMessage={errorMessage}
            tracedIndex={tracedIndex}
            onTrace={setTracedIndex}
            lang={lang}
          />
        )}
      </div>
    </div>
  );
}

export default App;
