import { useState } from 'react';
import type { Hit } from '../lib/types';
import { DISH_LABEL, KIND_LABEL, REGION_LABEL, STRINGS, label, type Lang } from '../lib/i18n';
import { ChevronIcon, StampIcon } from './Icons';
import styles from './SourceCard.module.css';

const KIND_VAR: Record<string, string> = {
  receita: 'var(--kind-receita)',
  tecnica: 'var(--kind-tecnica)',
  texto: 'var(--kind-texto)',
};

interface Props {
  hit: Hit;
  index: number;
  traced: boolean;
  lang: Lang;
}

export function SourceCard({ hit, index, traced, lang }: Props) {
  const t = STRINGS[lang];
  const [open, setOpen] = useState(false);
  const clear = open || traced;
  const kindColor = KIND_VAR[hit.kind] ?? 'var(--kind-texto)';
  const shownDishTypes = hit.dish_types.slice(0, 2);
  const extraDishTypes = hit.dish_types.length - shownDishTypes.length;
  const dishLabels =
    shownDishTypes.map((d) => label(DISH_LABEL[lang], d)).join(', ') + (extraDishTypes > 0 ? ` +${extraDishTypes}` : '');

  return (
    <div
      id={`source-${index}`}
      className={`${styles.card}${traced ? ` ${styles['card--traced']}` : ''}`}
      style={{ animationDelay: `${Math.min(index, 10) * 45}ms` }}
    >
      <button className={styles.head} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={styles.number}>[{index}]</span>
        <span className={styles.titles}>
          <div className={styles.title}>{hit.title || hit.book}</div>
          <div className={styles.book}>{hit.book}</div>
        </span>
        <span className={styles.badge} style={{ color: kindColor, borderColor: kindColor }}>
          {label(KIND_LABEL[lang], hit.kind)}
        </span>
        <ChevronIcon size={14} className={`${styles.chevron}${open ? ` ${styles['chevron--open']}` : ''}`} />
      </button>

      <div className={styles.meta}>
        <span>{label(REGION_LABEL[lang], hit.region)}</span>
        {dishLabels && <span>· {dishLabels}</span>}
        <span>· {hit.lang}</span>
        <span>· score {hit.score.toFixed(2)}</span>
        {hit.low_quality && (
          <span className={styles.lowQuality}>
            <StampIcon /> {t.lowQuality}
          </span>
        )}
      </div>

      {hit.ingredients.length > 0 && (
        <div className={styles.ingredients}>
          <b>{t.detectedIngredients}</b> {hit.ingredients.slice(0, 15).join(', ')}
        </div>
      )}

      <div className={`${styles.body}${clear ? ` ${styles['body--clear']}` : ''}`}>{hit.text}</div>
    </div>
  );
}
