import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import type { Filters } from '../lib/types';
import { DISH_LABEL, KIND_LABEL, REGION_LABEL, STRINGS, fill, label, type Lang } from '../lib/i18n';
import { ChevronIcon, CloseIcon, SearchIcon, SlidersIcon } from './Icons';
import { IngredientTagInput } from './IngredientTagInput';
import styles from './Composer.module.css';

export interface QueryState {
  query: string;
  ingredients: string[];
  kind: string;
  region: string;
  dishType: string;
  k: number;
}

export const DEFAULT_K = 8;

interface Props {
  state: QueryState;
  onChange: (next: QueryState) => void;
  onSubmit: () => void;
  filters: Filters | null;
  busy: boolean;
  turns: number;
  onNewConversation: () => void;
  lang: Lang;
}

const MAX_ROWS_PX = 180;

/** A barra de perguntas: o painel em uso, sempre nítido. Filtros recolhidos
 * atrás de um botão; os ativos ficam visíveis como etiquetas removíveis. */
export function Composer({ state, onChange, onSubmit, filters, busy, turns, onNewConversation, lang }: Props) {
  const t = STRINGS[lang];
  const [filtersOpen, setFiltersOpen] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const set = <K extends keyof QueryState>(key: K, value: QueryState[K]) => onChange({ ...state, [key]: value });

  // cresce com o texto até um teto, depois rola
  useLayoutEffect(() => {
    const el = textRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_ROWS_PX)}px`;
  }, [state.query]);

  useEffect(() => {
    if (!busy) textRef.current?.focus();
  }, [busy]);

  const canSend = !busy && state.query.trim().length > 0;
  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (canSend) onSubmit();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const chips: { key: string; text: string; clear: () => void }[] = [
    ...state.ingredients.map((ing) => ({
      key: `ing-${ing}`,
      text: ing,
      clear: () => set('ingredients', state.ingredients.filter((v) => v !== ing)),
    })),
    ...(state.kind ? [{ key: 'kind', text: label(KIND_LABEL[lang], state.kind), clear: () => set('kind', '') }] : []),
    ...(state.region ? [{ key: 'region', text: label(REGION_LABEL[lang], state.region), clear: () => set('region', '') }] : []),
    ...(state.dishType
      ? [{ key: 'dish', text: label(DISH_LABEL[lang], state.dishType), clear: () => set('dishType', '') }]
      : []),
    ...(state.k !== DEFAULT_K
      ? [{ key: 'k', text: `${state.k} ${t.excerptsLabel}`, clear: () => set('k', DEFAULT_K) }]
      : []),
  ];
  const clearAll = () => onChange({ ...state, ingredients: [], kind: '', region: '', dishType: '', k: DEFAULT_K });

  return (
    <form className={styles.composer} onSubmit={submit}>
      <div id="composer-filters" className={`${styles.filtersWrap}${filtersOpen ? ` ${styles.filtersOpen}` : ''}`}>
        <div className={styles.filtersInner} inert={!filtersOpen}>
          <div className={styles.filtersGrid}>
            <IngredientTagInput
              value={state.ingredients}
              onChange={(v) => set('ingredients', v)}
              placeholder={t.ingredientPlaceholder}
              ariaLabel={t.ingredientAriaLabel}
              removeLabelTemplate={t.removeIngredient}
            />
            <Select value={state.kind} onChange={(v) => set('kind', v)} aria={t.kindAriaLabel} all={t.allKinds}
              options={(filters?.kinds ?? []).map((k) => [k, label(KIND_LABEL[lang], k)])} />
            <Select value={state.region} onChange={(v) => set('region', v)} aria={t.regionAriaLabel} all={t.allRegions}
              options={(filters?.regions ?? []).map((r) => [r, label(REGION_LABEL[lang], r)])} />
            <Select value={state.dishType} onChange={(v) => set('dishType', v)} aria={t.dishTypeAriaLabel} all={t.allDishTypes}
              options={(filters?.dish_types ?? []).map((d) => [d, label(DISH_LABEL[lang], d)])} />
            <label className={styles.kField}>
              <span className={styles.kLabel}>{t.excerptsLabel}</span>
              <input className={styles.kRange} type="range" min={3} max={20} value={state.k}
                onChange={(e) => set('k', Number(e.target.value))} aria-label={t.excerptsAriaLabel} />
              <span className={`${styles.kValue} tabular`}>{state.k}</span>
            </label>
          </div>
        </div>
      </div>

      <div className={styles.inputRow}>
        <textarea
          ref={textRef}
          className={styles.input}
          rows={1}
          value={state.query}
          onChange={(e) => set('query', e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={t.composerPlaceholder}
          aria-label={t.queryAriaLabel}
        />
        <button className={styles.askButton} type="submit" disabled={!canSend}>
          <SearchIcon size={16} />
          <span className={styles.askLabel}>{busy ? t.askButtonBusy : t.askButton}</span>
        </button>
      </div>

      <div className={styles.toolbar}>
        <button
          type="button"
          className={`${styles.toolButton}${filtersOpen ? ` ${styles.toolButtonActive}` : ''}`}
          aria-expanded={filtersOpen}
          aria-controls="composer-filters"
          onClick={() => setFiltersOpen((v) => !v)}
        >
          <SlidersIcon size={14} />
          {t.filtersButton}
          {chips.length > 0 && <span className={`${styles.count} tabular`}>{chips.length}</span>}
          <ChevronIcon size={12} className={`${styles.chev}${filtersOpen ? ` ${styles.chevOpen}` : ''}`} />
        </button>

        {chips.map((c) => (
          <span key={c.key} className={styles.chip}>
            {c.text}
            <button type="button" onClick={c.clear} aria-label={`${t.clearFilters}: ${c.text}`}>
              <CloseIcon size={10} />
            </button>
          </span>
        ))}
        {chips.length > 1 && (
          <button type="button" className={styles.linkButton} onClick={clearAll}>
            {t.clearFilters}
          </button>
        )}

        <span className={styles.spacer} />

        {turns > 0 ? (
          <span className={styles.conversation}>
            <span className={styles.turns}>{turns === 1 ? t.conversationTurnsOne : fill(t.conversationTurns, { n: turns })}</span>
            <button type="button" className={styles.linkButton} onClick={onNewConversation} disabled={busy}>
              {t.newConversation}
            </button>
          </span>
        ) : (
          <span className={styles.hint}>{t.composerHint}</span>
        )}
      </div>
    </form>
  );
}

function Select({ value, onChange, aria, all, options }: {
  value: string;
  onChange: (v: string) => void;
  aria: string;
  all: string;
  options: [string, string][];
}) {
  return (
    <div className={styles.field}>
      <select className={styles.select} value={value} onChange={(e) => onChange(e.target.value)} aria-label={aria}>
        <option value="">{all}</option>
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
      <ChevronIcon size={13} className={styles.selectChevron} />
    </div>
  );
}
