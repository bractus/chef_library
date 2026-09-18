import { useState, type KeyboardEvent } from 'react';
import { DropletIcon } from './Icons';
import styles from './IngredientTagInput.module.css';

interface Props {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
  ariaLabel: string;
  removeLabelTemplate: string;
}

/** Campo de ingredientes com chips: digita um ingrediente, Enter/vírgula
 * confirma, Backspace no campo vazio remove o último. Filtra receitas que
 * contêm TODOS os ingredientes adicionados (ver backend/store.py). */
export function IngredientTagInput({ value, onChange, placeholder, ariaLabel, removeLabelTemplate }: Props) {
  const [draft, setDraft] = useState('');

  const commit = () => {
    const v = draft.trim();
    if (v && !value.includes(v)) onChange([...value, v]);
    setDraft('');
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      commit();
    } else if (e.key === 'Backspace' && !draft && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  };

  return (
    <div className={styles.wrap}>
      <DropletIcon size={14} className={styles.icon} />
      {value.map((ing) => (
        <span key={ing} className={styles.chip}>
          {ing}
          <button
            type="button"
            className={styles.chipRemove}
            onClick={() => onChange(value.filter((v) => v !== ing))}
            aria-label={removeLabelTemplate.replace('{ing}', ing)}
          >
            ×
          </button>
        </span>
      ))}
      <input
        className={styles.input}
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={commit}
        placeholder={value.length === 0 ? placeholder : ''}
        aria-label={ariaLabel}
      />
    </div>
  );
}
