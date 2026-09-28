import { useEffect, useState } from 'react';
import type { ChatTurn } from '../lib/types';
import { renderMarkdown } from '../lib/inline';
import { STRINGS, fill, type Lang } from '../lib/i18n';
import { BookIcon, CheckIcon, CopyIcon, GlobeIcon, WarningIcon } from './Icons';
import styles from './ChatAnswer.module.css';

interface Props {
  turn: ChatTurn;
  focused: boolean;
  tracedIndex: number | null;
  onTrace: (n: number | null) => void;
  onShowSources: () => void;
  lang: Lang;
}

/** Resposta do chef, sem painel: texto corrido na coluna, como num chat.
 * Respostas fora de foco ficam em repouso (mais apagadas) até o hover. */
export function ChatAnswer({ turn, focused, tracedIndex, onTrace, onShowSources, lang }: Props) {
  const t = STRINGS[lang];
  const [copied, setCopied] = useState(false);
  const streaming = turn.phase === 'streaming';
  const nSources = turn.hits.length + turn.webSources.length;

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(turn.answer);
      setCopied(true);
    } catch {
      /* sem permissão de área de transferência: o botão só não confirma */
    }
  };

  if (turn.phase === 'error') {
    return (
      <div className={styles.error} role="alert">
        <WarningIcon size={16} />
        <span>{turn.error}</span>
      </div>
    );
  }

  if (turn.phase === 'loading') {
    return (
      <div className={styles.answer} aria-busy="true">
        <div className={styles.skeleton} aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <Status text={t.searchingArchive} />
      </div>
    );
  }

  return (
    <div className={`${styles.answer}${focused ? '' : ` ${styles.rest}`}`}>
      {!turn.grounded && (
        <div className={styles.fallbackBanner}>
          <GlobeIcon size={13} />
          {t.fallbackBanner}
        </div>
      )}
      <div className={styles.prose}>
        {renderMarkdown(turn.answer, onTrace, focused ? tracedIndex : null, (isLast) =>
          isLast && streaming ? styles.reveal : '',
        )}
        {streaming && <span className={styles.cursor} aria-hidden="true" />}
      </div>

      {streaming ? (
        <Status text={turn.searching ? fill(t.searchingWeb, { query: turn.searching }) : t.streamingFootnote} />
      ) : (
        turn.answer.trim() && (
          <div className={styles.actions}>
            {nSources > 0 && (
              <button type="button" className={styles.action} onClick={onShowSources}>
                <BookIcon size={13} />
                {fill(t.showSources, { n: nSources })}
              </button>
            )}
            <button type="button" className={styles.action} onClick={copy} aria-live="polite">
              {copied ? <CheckIcon size={13} /> : <CopyIcon size={13} />}
              {copied ? t.copied : t.copyAnswer}
            </button>
          </div>
        )
      )}
    </div>
  );
}

function Status({ text }: { text: string }) {
  return (
    <div className={styles.status}>
      <span className={styles.dots} aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      {text}
    </div>
  );
}
