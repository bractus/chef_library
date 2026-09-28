import { useEffect, useRef, useState } from 'react';
import type { ChatTurn, Hit, WebSource } from '../lib/types';
import { DISH_LABEL, KIND_LABEL, REGION_LABEL, STRINGS, fill, label, type Lang } from '../lib/i18n';
import { ChevronIcon, CloseIcon, GlobeIcon, StampIcon } from './Icons';
import styles from './SourcesRail.module.css';

const KIND_VAR: Record<string, string> = {
  receita: 'var(--kind-receita)',
  tecnica: 'var(--kind-tecnica)',
  texto: 'var(--kind-texto)',
};

interface Props {
  turn: ChatTurn | null;
  turnNumber: number;
  tracedIndex: number | null;
  onTrace: (n: number | null) => void;
  open: boolean;
  onClose: () => void;
  lang: Lang;
}

function domain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

/** Fontes da resposta em foco, à direita: consultáveis, mas em repouso — só
 * acendem quando uma citação da resposta é rastreada. Em tela estreita vira
 * gaveta. */
export function SourcesRail({ turn, turnNumber, tracedIndex, onTrace, open, onClose, lang }: Props) {
  const t = STRINGS[lang];
  const hits = turn?.hits ?? [];
  const web = turn?.webSources ?? [];
  const total = hits.length + web.length;

  return (
    <>
      <div className={`${styles.scrim}${open ? ` ${styles.scrimOpen}` : ''}`} onClick={onClose} aria-hidden="true" />
      <aside className={`${styles.rail}${open ? ` ${styles.railOpen}` : ''}`} aria-label={t.sourcesHeading}>
        <header className={styles.head}>
          <h2 className={styles.title}>
            {t.sourcesHeading}
            {total > 0 && <span className={`${styles.total} tabular`}>{total}</span>}
          </h2>
          {total > 0 && <span className={styles.which}>{fill(t.sourcesForQuestion, { n: turnNumber })}</span>}
          <button type="button" className={styles.close} onClick={onClose} aria-label={t.closeSources}>
            <CloseIcon size={14} />
          </button>
        </header>

        {total === 0 ? (
          <p className={styles.empty}>{t.sourcesEmpty}</p>
        ) : (
          <ol className={styles.list} key={turn?.id}>
            {hits.map((h, i) => (
              <HitItem key={h.id} hit={h} n={i + 1} traced={tracedIndex === i + 1} onTrace={onTrace} lang={lang} />
            ))}
            {web.map((w) => (
              <WebItem key={`${w.index}-${w.url}`} source={w} traced={tracedIndex === w.index} onTrace={onTrace} lang={lang} />
            ))}
          </ol>
        )}
      </aside>
    </>
  );
}

function useTraceScroll(traced: boolean) {
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (traced) ref.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [traced]);
  return ref;
}

function HitItem({ hit, n, traced, onTrace, lang }: {
  hit: Hit; n: number; traced: boolean; onTrace: (n: number | null) => void; lang: Lang;
}) {
  const t = STRINGS[lang];
  const [open, setOpen] = useState(false);
  const ref = useTraceScroll(traced);
  const dishes = hit.dish_types.slice(0, 2).map((d) => label(DISH_LABEL[lang], d)).join(', ');

  return (
    <li
      ref={ref}
      id={`source-${n}`}
      className={`${styles.item}${traced ? ` ${styles.traced}` : ''}${open ? ` ${styles.itemOpen}` : ''}`}
      style={{ animationDelay: `${Math.min(n, 10) * 40}ms` }}
      onMouseEnter={() => onTrace(n)}
      onMouseLeave={() => onTrace(null)}
    >
      <button type="button" className={styles.row} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={`${styles.n} tabular`}>{n}</span>
        <span className={styles.titles}>
          <span className={styles.itemTitle}>{hit.title || hit.book}</span>
          <span className={styles.origin}>
            <span className={styles.kindDot} style={{ background: KIND_VAR[hit.kind] ?? 'var(--kind-texto)' }} />
            {hit.book}
          </span>
        </span>
        <ChevronIcon size={12} className={`${styles.chev}${open ? ` ${styles.chevOpen}` : ''}`} />
      </button>
      {open && (
        <div className={styles.detail}>
          <div className={styles.meta}>
            <span style={{ color: KIND_VAR[hit.kind] ?? 'var(--kind-texto)' }}>{label(KIND_LABEL[lang], hit.kind)}</span>
            <span>{label(REGION_LABEL[lang], hit.region)}</span>
            {dishes && <span>{dishes}</span>}
            <span>{hit.lang}</span>
            <span className="tabular">score {hit.score.toFixed(2)}</span>
            {hit.low_quality && (
              <span className={styles.lowQuality}>
                <StampIcon /> {t.lowQuality}
              </span>
            )}
          </div>
          {hit.ingredients.length > 0 && (
            <p className={styles.ingredients}>
              <b>{t.detectedIngredients}</b> {hit.ingredients.slice(0, 15).join(', ')}
            </p>
          )}
          <p className={styles.excerpt}>{hit.text}</p>
        </div>
      )}
    </li>
  );
}

function WebItem({ source, traced, onTrace, lang }: {
  source: WebSource; traced: boolean; onTrace: (n: number | null) => void; lang: Lang;
}) {
  const t = STRINGS[lang];
  const [open, setOpen] = useState(false);
  const ref = useTraceScroll(traced);

  return (
    <li
      ref={ref}
      id={`source-${source.index}`}
      className={`${styles.item}${traced ? ` ${styles.traced}` : ''}${open ? ` ${styles.itemOpen}` : ''}`}
      style={{ animationDelay: `${Math.min(source.index, 10) * 40}ms` }}
      onMouseEnter={() => onTrace(source.index)}
      onMouseLeave={() => onTrace(null)}
    >
      <button type="button" className={styles.row} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={`${styles.n} tabular`}>{source.index}</span>
        <span className={styles.titles}>
          <span className={styles.itemTitle}>{source.title || domain(source.url)}</span>
          <span className={styles.origin}>
            <GlobeIcon size={11} className={styles.webIcon} />
            {domain(source.url)}
          </span>
        </span>
        <ChevronIcon size={12} className={`${styles.chev}${open ? ` ${styles.chevOpen}` : ''}`} />
      </button>
      {open && (
        <div className={styles.detail}>
          <p className={styles.excerpt}>{source.content}</p>
          <a className={styles.link} href={source.url} target="_blank" rel="noopener noreferrer">
            {t.openLink} ↗
          </a>
        </div>
      )}
    </li>
  );
}
