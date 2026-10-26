import { describe, expect, it } from 'vitest';
import { useSubjectStore } from '../src/stores/subjectStore';
import type { Subject } from '../src/types/api';

function sub(id: string, name: string, isDefault = false): Subject {
  return {
    id,
    name,
    relation: '本人',
    color: null,
    avatar: null,
    isDefault,
    birthday: null,
    gender: null,
  };
}

describe('subject store 切换（F-02/E-8）', () => {
  it('无守卫时直接切换并记忆', () => {
    useSubjectStore.setState({
      subjects: [sub('s1', '本人', true), sub('s2', '父亲')],
      current: sub('s1', '本人', true),
      guardActive: false,
      pendingSwitch: null,
    });
    const result = useSubjectStore.getState().selectSubject('s2');
    expect(result).toBe('ok');
    expect(useSubjectStore.getState().current?.id).toBe('s2');
  });

  it('守卫开启时切换进入 pending 阻断（E-8）', () => {
    useSubjectStore.setState({
      subjects: [sub('s1', '本人', true), sub('s2', '父亲')],
      current: sub('s1', '本人', true),
      guardActive: true,
      pendingSwitch: null,
    });
    const result = useSubjectStore.getState().selectSubject('s2');
    expect(result).toBe('pending');
    const state = useSubjectStore.getState();
    expect(state.pendingSwitch?.to.id).toBe('s2');
    expect(state.current?.id).toBe('s1'); // 未真正切换
    state.commitPendingSwitch();
    expect(useSubjectStore.getState().current?.id).toBe('s2');
    expect(useSubjectStore.getState().pendingSwitch).toBeNull();
  });

  it('取消 pending 保持当前档案', () => {
    useSubjectStore.setState({
      subjects: [sub('s1', '本人', true), sub('s2', '父亲')],
      current: sub('s1', '本人', true),
      guardActive: true,
      pendingSwitch: { from: sub('s1', '本人', true), to: sub('s2', '父亲') },
    });
    useSubjectStore.getState().cancelPendingSwitch();
    const state = useSubjectStore.getState();
    expect(state.current?.id).toBe('s1');
    expect(state.pendingSwitch).toBeNull();
  });

  it('未知 subject 返回 missing（零串档保护）', () => {
    useSubjectStore.setState({
      subjects: [sub('s1', '本人', true)],
      current: sub('s1', '本人', true),
      guardActive: false,
      pendingSwitch: null,
    });
    expect(useSubjectStore.getState().selectSubject('nope')).toBe('missing');
  });

  it('applyRemoteSubjects 优先上次选中', () => {
    useSubjectStore.setState({ subjects: [], current: null, guardActive: false, pendingSwitch: null });
    useSubjectStore.getState().applyRemoteSubjects(
      [sub('s1', '本人', true), sub('s2', '父亲')],
      's2',
    );
    expect(useSubjectStore.getState().current?.id).toBe('s2');
  });
});
