import styles from './VaporField.module.css';

/** Vapor ambiente atras de tudo — puro CSS (dois gradientes radiais em
 * deriva lenta), o "ar" da linha de cozinha por tras do vidro. Nunca chama
 * atencao para si mesmo: opacidade baixa, movimento lento, sem interacao. */
export function VaporField() {
  return (
    <div className={styles.field} aria-hidden="true">
      <div className={styles.wisp1} />
      <div className={styles.wisp2} />
      <div className={styles.grain} />
    </div>
  );
}
