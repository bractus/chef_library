import type { Hit } from '../lib/types';
import { AnswerPane } from './AnswerPane';
import { SourceCard } from './SourceCard';
import { WarningIcon } from './Icons';
import { STRINGS, type Lang } from '../lib/i18n';
import styles from './ResultsArea.module.css';

export type Phase = 'idle' | 'loading' | 'error' | 'answering';

interface Props {
  phase: Phase;
  answer: string;
  streaming: boolean;
  hits: Hit[];
  grounded: boolean;
  errorMessage: string | null;
  tracedIndex: number | null;
  onTrace: (n: number | null) => void;
  lang: Lang;
}

export function ResultsArea({
  phase,
  answer,
  streaming,
  hits,
  grounded,
  errorMessage,
  tracedIndex,
  onTrace,
  lang,
}: Props) {
  const t = STRINGS[lang];

  if (phase === 'idle' || phase === 'loading') {
    return (
      <div className={styles.wrap}>
        <div className={styles.idle}>
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className={styles.ghost} style={phase === 'loading' ? { animation: 'none' } : undefined} />
          ))}
          {phase === 'loading' && <div className={styles.idlePrompt}>{t.loadingMessage}</div>}
        </div>
      </div>
    );
  }

  if (phase === 'error') {
    return (
      <div className={styles.wrap}>
        <div className={styles.errorPane}>
          <WarningIcon size={18} />
          <span>{errorMessage}</span>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.wrap}>
      <AnswerPane
        text={answer}
        streaming={streaming}
        tracedIndex={tracedIndex}
        onTrace={onTrace}
        grounded={grounded}
        lang={lang}
      />

      {hits.length > 0 && (
        <div>
          <div className={styles.sourcesHeading}>
            {t.sourcesHeading} ({hits.length})
          </div>
          <div className={styles.sourcesGrid}>
            {hits.map((h, i) => (
              <SourceCard key={h.id} hit={h} index={i + 1} traced={tracedIndex === i + 1} lang={lang} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
