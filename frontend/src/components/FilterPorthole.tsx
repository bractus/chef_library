import { type FormEvent, type KeyboardEvent } from 'react';
import type { Filters } from '../lib/types';
import { DISH_LABEL, KIND_LABEL, REGION_LABEL, STRINGS, label, type Lang } from '../lib/i18n';
import { ChevronIcon, SearchIcon } from './Icons';
import { IngredientTagInput } from './IngredientTagInput';
import styles from './FilterPorthole.module.css';

export interface QueryState {
  query: string;
  ingredients: string[];
  kind: string;
  region: string;
  dishType: string;
  k: number;
}

interface Props {
  state: QueryState;
  onChange: (next: QueryState) => void;
  onSubmit: () => void;
  filters: Filters | null;
  busy: boolean;
  lang: Lang;
}

export function FilterPorthole({ state, onChange, onSubmit, filters, busy, lang }: Props) {
  const t = STRINGS[lang];
  const set = <K extends keyof QueryState>(key: K, value: QueryState[K]) =>
    onChange({ ...state, [key]: value });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (state.query.trim() && !busy) onSubmit();
  };

  const handleKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleSubmit(e as unknown as FormEvent);
  };

  return (
    <form className={styles.porthole} onSubmit={handleSubmit}>
      <div className={styles.askRow}>
        <input
          className={styles.queryInput}
          type="text"
          value={state.query}
          onChange={(e) => set('query', e.target.value)}
          onKeyDown={handleKey}
          placeholder={t.queryPlaceholder}
          aria-label={t.queryAriaLabel}
        />
        <button className={styles.askButton} type="submit" disabled={busy || !state.query.trim()}>
          <SearchIcon size={16} />
          {busy ? t.askButtonBusy : t.askButton}
        </button>
      </div>

      <div className={styles.filtersRow}>
        <IngredientTagInput
          value={state.ingredients}
          onChange={(v) => set('ingredients', v)}
          placeholder={t.ingredientPlaceholder}
          ariaLabel={t.ingredientAriaLabel}
          removeLabelTemplate={t.removeIngredient}
        />

        <div className={styles.field}>
          <select
            className={styles.select}
            value={state.kind}
            onChange={(e) => set('kind', e.target.value)}
            aria-label={t.kindAriaLabel}
          >
            <option value="">{t.allKinds}</option>
            {(filters?.kinds ?? []).map((k) => (
              <option key={k} value={k}>
                {label(KIND_LABEL[lang], k)}
              </option>
            ))}
          </select>
          <ChevronIcon size={13} className={styles.selectChevron} />
        </div>

        <div className={styles.field}>
          <select
            className={styles.select}
            value={state.region}
            onChange={(e) => set('region', e.target.value)}
            aria-label={t.regionAriaLabel}
          >
            <option value="">{t.allRegions}</option>
            {(filters?.regions ?? []).map((r) => (
              <option key={r} value={r}>
                {label(REGION_LABEL[lang], r)}
              </option>
            ))}
          </select>
          <ChevronIcon size={13} className={styles.selectChevron} />
        </div>

        <div className={styles.field}>
          <select
            className={styles.select}
            value={state.dishType}
            onChange={(e) => set('dishType', e.target.value)}
            aria-label={t.dishTypeAriaLabel}
          >
            <option value="">{t.allDishTypes}</option>
            {(filters?.dish_types ?? []).map((d) => (
              <option key={d} value={d}>
                {label(DISH_LABEL[lang], d)}
              </option>
            ))}
          </select>
          <ChevronIcon size={13} className={styles.selectChevron} />
        </div>

        <div className={styles.kField}>
          <span className={styles.kLabel}>{t.excerptsLabel}</span>
          <input
            className={styles.kRange}
            type="range"
            min={3}
            max={20}
            value={state.k}
            onChange={(e) => set('k', Number(e.target.value))}
            aria-label={t.excerptsAriaLabel}
          />
          <span className={`${styles.kValue} tabular`}>{state.k}</span>
        </div>
      </div>
    </form>
  );
}
