import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { Icon } from './WorkVisuals';
import './workReading.css';

type Props = { artifactVersion: number; requirementVersion: number; artifact: ReactNode; requirement: ReactNode };

export default function WorkSourceReading({ artifactVersion, requirementVersion, artifact, requirement }: Props) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const [selected, setSelected] = useState<'artifact' | 'requirement'>('requirement');
  const labels = {
    artifact: (zh ? '成果' : 'Artifact') + ' v' + artifactVersion,
    requirement: (zh ? '对应要求' : 'Original requirement') + ' v' + requirementVersion,
  };
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, []);
  return <div className="work-source-reading" data-selected={selected}>
    <div className="source-reading-bar">
      <h2 ref={heading} tabIndex={-1}><Icon name="link" size={18} />{zh ? '对照阅读' : 'Compare documents'}</h2>
      <div className="source-switches" role="group" aria-label={zh ? '选择文稿' : 'Choose document'}>
        {(['artifact', 'requirement'] as const).map(side =>
          <button key={side} type="button" aria-pressed={side === selected} aria-controls={id + side}
            onClick={() => setSelected(side)}>{labels[side]}</button>)}
      </div>
    </div>
    <div className="source-spread">
      <section id={id + 'artifact'} className="source-sheet" data-side="artifact" aria-label={labels.artifact}>
        <h3><Icon name="artifact" size={16} />{labels.artifact}</h3>{artifact}
      </section>
      <section id={id + 'requirement'} className="source-sheet" data-side="requirement" aria-label={labels.requirement}>
        <h3><Icon name="requirement" size={16} />{labels.requirement}</h3>{requirement}
      </section>
    </div>
  </div>;
}
