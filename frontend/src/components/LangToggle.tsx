import type { Lang } from '../lib/i18n';
import styles from './LangToggle.module.css';

const OPTIONS: { value: Lang; label: string }[] = [
  { value: 'pt', label: 'PT' },
  { value: 'en', label: 'EN' },
];

export function LangToggle({ lang, onChange }: { lang: Lang; onChange: (lang: Lang) => void }) {
  return (
    <div className={styles.toggle} role="group" aria-label="Language / Idioma">
      {OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          className={`${styles.option}${lang === opt.value ? ` ${styles.optionActive}` : ''}`}
          aria-pressed={lang === opt.value}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
