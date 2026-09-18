import type { Stats } from '../lib/types';
import type { Lang } from '../lib/i18n';
import { STRINGS } from '../lib/i18n';
import styles from './StatsGauge.module.css';

const fmt = new Intl.NumberFormat('en-US');

export function StatsGauge({ stats, lang }: { stats: Stats | null; lang: Lang }) {
  if (!stats) {
    return (
      <div className={`${styles.gauge} ${styles.skeleton}`}>
        <span className="tabular">···</span>
      </div>
    );
  }
  return (
    <div className={styles.gauge}>
      <span className={`${styles.value} tabular`}>{fmt.format(stats.books)}</span> {STRINGS[lang].booksUnit}
    </div>
  );
}
