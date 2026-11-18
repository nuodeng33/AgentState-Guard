import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { Icon } from './WorkVisuals';
import './workReading.css';

type Document = { side: 'artifact' | 'requirement' | 'review'; label: string; content: ReactNode };
type Props = { left: Document; right: Document; initialSide: Document['side'] };

export default function WorkSourceReading({ left, right, initialSide }: Props) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const [selected, setSelected] = useState(initialSide);
  const documents = [left, right];

  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, []);

  return <div className="work-source-reading" data-selected={selected}>
    <div className="source-reading-bar">
      <h2 ref={heading} tabIndex={-1}>{zh ? '对照阅读' : 'Compare documents'}</h2>
      <div className="source-switches" role="group" aria-label={zh ? '选择文稿' : 'Choose document'}>
        {documents.map(({ side, label }) =>
          <button key={side} type="button" aria-pressed={side === selected} aria-controls={id + side}
            onClick={() => setSelected(side)}>{label}</button>
        )}
      </div>
    </div>
    <div className="source-spread">
      {documents.map(({ side, label, content }) => <section key={side} id={id + side} className="source-sheet"
        data-side={side} data-current={side === selected} aria-labelledby={id + side + '-title'}>
        <h3 id={id + side + '-title'}><Icon name={side} size={16} />{label}</h3>{content}
      </section>)}
    </div>
  </div>;
}
