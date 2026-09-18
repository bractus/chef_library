import { renderMarkdown } from '../lib/inline';
import { STRINGS, type Lang } from '../lib/i18n';
import { GlobeIcon } from './Icons';
import styles from './AnswerPane.module.css';

interface Props {
  text: string;
  streaming: boolean;
  tracedIndex: number | null;
  onTrace: (n: number | null) => void;
  grounded: boolean;
  lang: Lang;
}

export function AnswerPane({ text, streaming, tracedIndex, onTrace, grounded, lang }: Props) {
  const t = STRINGS[lang];
  return (
    <div className={styles.pane}>
      {!grounded && (
        <div className={styles.fallbackBanner}>
          <GlobeIcon size={13} />
          {t.fallbackBanner}
        </div>
      )}
      <div className={styles.prose}>
        {renderMarkdown(text, onTrace, tracedIndex, (isLast) => (isLast ? styles.reveal : ''))}
        {streaming && <span className={styles.cursor} aria-hidden="true" />}
      </div>
      {streaming && (
        <div className={styles.footRow}>
          <span className={styles.dots}>
            <span />
            <span />
            <span />
          </span>
          {t.streamingFootnote}
        </div>
      )}
    </div>
  );
}
