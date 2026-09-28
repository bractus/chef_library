import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import type { ChatTurn } from '../lib/types';
import { STRINGS, type Lang } from '../lib/i18n';
import { ChatAnswer } from './ChatAnswer';
import { SourcesRail } from './SourcesRail';
import styles from './ChatView.module.css';

interface Props {
  turns: ChatTurn[];
  composer: ReactNode;
  indexReady: boolean | null;
  onExample: (question: string) => void;
  lang: Lang;
}

const STICK_THRESHOLD_PX = 120;
const DRAWER_QUERY = '(max-width: 1000px)';

/** Chat: histórico acima, barra de perguntas embaixo, fontes à direita. */
export function ChatView({ turns, composer, indexReady, onExample, lang }: Props) {
  const t = STRINGS[lang];
  const scrollRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [traced, setTraced] = useState<number | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const last = turns[turns.length - 1];
  // uma pergunta nova sempre traz o foco (e as fontes) para ela
  const [lastSeen, setLastSeen] = useState<string | undefined>(undefined);
  if (last?.id !== lastSeen) {
    setLastSeen(last?.id);
    setFocusedId(last?.id ?? null);
    setTraced(null);
  }
  const focusedIndex = Math.max(0, turns.findIndex((x) => x.id === focusedId));
  const focused = turns[focusedIndex] ?? null;

  // acompanha o fim da conversa enquanto a resposta chega, a menos que o
  // usuário tenha rolado para cima para reler algo
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });

  useEffect(() => {
    stick.current = true;
  }, [turns.length]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX;
  };

  const trace = (turnId: string, n: number | null) => {
    if (n !== null && turnId !== focusedId) setFocusedId(turnId);
    setTraced(n);
  };

  const showSources = (turnId: string) => {
    setFocusedId(turnId);
    // no desktop o trilho ja esta visivel; a gaveta so existe em tela estreita
    if (window.matchMedia(DRAWER_QUERY).matches) setDrawerOpen(true);
  };

  return (
    <div className={styles.chat}>
      <section className={styles.thread}>
        <div className={styles.scroll} ref={scrollRef} onScroll={onScroll}>
          <div className={styles.inner}>
            {turns.length === 0 ? (
              <Empty indexReady={indexReady} onExample={onExample} lang={lang} />
            ) : (
              turns.map((turn) => (
                <article key={turn.id} className={styles.turn}>
                  <div className={styles.userRow}>
                    <p className={styles.bubble}>
                      <span className={styles.srOnly}>{t.youLabel}: </span>
                      {turn.question}
                    </p>
                  </div>
                  <ChatAnswer
                    turn={turn}
                    focused={turn.id === focused?.id}
                    tracedIndex={turn.id === focused?.id ? traced : null}
                    onTrace={(n) => trace(turn.id, n)}
                    onShowSources={() => showSources(turn.id)}
                    lang={lang}
                  />
                </article>
              ))
            )}
          </div>
        </div>
        <div className={styles.dock}>
          <div className={styles.dockInner}>{composer}</div>
        </div>
      </section>

      <SourcesRail
        turn={focused}
        turnNumber={focusedIndex + 1}
        tracedIndex={traced}
        onTrace={setTraced}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        lang={lang}
      />
    </div>
  );
}

function Empty({ indexReady, onExample, lang }: { indexReady: boolean | null; onExample: (q: string) => void; lang: Lang }) {
  const t = STRINGS[lang];
  if (indexReady === false) {
    return (
      <div className={styles.empty}>
        <h1 className={styles.emptyTitle}>{t.setupTitle}</h1>
        <p className={styles.emptyBody}>{t.setupBody1}</p>
        <pre className={styles.code}>python -m backend.ingest.build_index</pre>
        <p className={styles.emptyBody}>
          <a href="#/adicionar">{t.setupIngestHint}</a>
        </p>
      </div>
    );
  }
  return (
    <div className={styles.empty}>
      <h1 className={styles.emptyTitle}>{t.emptyTitle}</h1>
      <p className={styles.emptyBody}>{t.emptyBody}</p>
      <div className={styles.examples}>
        {t.examples.map((q) => (
          <button key={q} type="button" className={styles.example} onClick={() => onExample(q)}>
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
