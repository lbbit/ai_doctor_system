import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { timelineApi } from '../api/timeline';
import { observationsApi } from '../api/observations';
import { useSubjectStore } from '../stores/subjectStore';
import { EVENT_TYPE_LABELS, TrendOption, TrendResponse } from '../types/api';

const EVENT_TYPE_OPTIONS = Object.entries(EVENT_TYPE_LABELS).map(([value, label]) => ({
  value,
  label,
}));

/** F-07/F-08 时间线 + TOP20 指标趋势折线（AC-7.x / AC-8.x）。 */
export default function TimelinePage() {
  const current = useSubjectStore((s) => s.current);
  const [types, setTypes] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [trendCode, setTrendCode] = useState('');

  const summary = useQuery({
    queryKey: ['timeline-summary', current?.id],
    queryFn: () => timelineApi.summary(current!.id),
    enabled: Boolean(current?.id),
  });

  const events = useQuery({
    queryKey: ['timeline-events', current?.id, types, dateFrom, dateTo],
    queryFn: () =>
      timelineApi.events({
        subjectId: current!.id,
        types: types || undefined,
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
        pageSize: 100,
      }),
    enabled: Boolean(current?.id),
  });

  const options = useQuery({
    queryKey: ['trend-options'],
    queryFn: () => observationsApi.trendOptions(),
  });

  const trend = useQuery({
    queryKey: ['trend', current?.id, trendCode],
    queryFn: () =>
      observationsApi.trend({ subjectId: current!.id, code: trendCode, limit: 20 }),
    enabled: Boolean(current?.id && trendCode),
  });

  const chartOption = useMemo<EChartsOption | null>(() => {
    const data = trend.data as TrendResponse | undefined;
    if (!data || data.insufficient || data.points.length < 2) return null;
    const dates = data.points.map((p) => p.occurredAt);
    const values = data.points.map((p) => p.valueNum);
    const markArea =
      data.refBand && (data.refBand.low != null || data.refBand.high != null)
        ? {
            silent: true,
            itemStyle: { color: 'rgba(59,130,246,0.08)' },
            data: [
              [
                { name: '参考区间', xAxis: 'min', yAxis: data.refBand.low ?? 'dataMin' },
                { xAxis: 'max', yAxis: data.refBand.high ?? 'dataMax' },
              ] as [
                { name: string; xAxis: string; yAxis: string | number },
                { xAxis: string; yAxis: string | number },
              ],
            ],
          }
        : undefined;
    return {
      tooltip: { trigger: 'axis' },
      grid: { left: 60, right: 30, top: 40, bottom: 40 },
      xAxis: { type: 'category', data: dates, name: '日期' },
      yAxis: {
        type: 'value',
        name: `${data.name}${data.unit ? `（${data.unit}）` : ''}`,
        scale: true,
      },
      series: [
        {
          type: 'line',
          data: values,
          smooth: false,
          symbolSize: 8,
          markArea,
          markPoint: {
            data: data.points
              .filter((p) => p.abnormalFlag && p.abnormalFlag !== 'N' && p.valueNum != null)
              .map((p) => ({
                name: p.abnormalFlag ?? '异常',
                coord: [p.occurredAt, p.valueNum as number],
                value: p.abnormalFlag ?? undefined,
              })),
          },
        },
      ],
      dataZoom: [{ type: 'inside' }],
    };
  }, [trend.data]);

  if (!current) return <div className="empty-state">请先选择档案对象</div>;

  const groups = events.data?.groups ?? [];

  return (
    <div className="timeline-page">
      <section className="panel">
        <header className="panel__header">
          <h2>指标趋势</h2>
          <select
            value={trendCode}
            onChange={(e) => setTrendCode(e.target.value)}
            aria-label="选择指标"
          >
            <option value="">选择指标…</option>
            {(options.data ?? []).map((o: TrendOption) => (
              <option key={o.code} value={o.code}>
                {o.name}
                {o.unit ? `（${o.unit}）` : ''}
              </option>
            ))}
          </select>
        </header>
        {trendCode && trend.isLoading && <p className="muted">加载中…</p>}
        {trendCode && trend.data?.insufficient && (
          <p className="notice notice--info">数据不足成线（仅 {trend.data.points.length} 条）</p>
        )}
        {chartOption && (
          <ReactECharts option={chartOption} style={{ height: 320 }} notMerge lazyUpdate />
        )}
        {trendCode && !trend.data?.insufficient && !chartOption && (
          <p className="muted">暂无数据</p>
        )}
      </section>

      <section className="panel">
        <header className="panel__header">
          <h2>事件时间线</h2>
          <div className="filters">
            <select value={types} onChange={(e) => setTypes(e.target.value)}>
              <option value="">全部类型</option>
              {EVENT_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
            <span className="muted">至</span>
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </div>
        </header>

        {summary.data?.isEmpty && (
          <div className="empty-state">
            <p>档案还是空的，先去上传一份报告吧。</p>
          </div>
        )}

        {groups.length === 0 && !summary.data?.isEmpty && (
          <p className="muted">暂无事件（可调整筛选条件）</p>
        )}

        <div className="timeline">
          {groups.map((g) => (
            <div key={`${g.year}-${g.month}`} className="timeline__group">
              <h3 className="timeline__group-title">
                {g.year} 年 {g.month ? `${g.month} 月` : ''}
              </h3>
              <ul>
                {g.items.map((item) => (
                  <li key={item.id} className="timeline__item">
                    <span className={`event-badge event-badge--${item.eventType}`}>
                      {EVENT_TYPE_LABELS[item.eventType] || item.eventType}
                    </span>
                    <div className="timeline__item-body">
                      <strong>{item.title || '（未命名事件）'}</strong>
                      {item.diagnosis && <span className="muted"> · 诊断：{item.diagnosis}</span>}
                      <div className="muted small">
                        {item.occurredAt}
                        {item.documentId && ' · 有原件'}
                        {item.observationCount > 0 && ` · ${item.observationCount} 项指标`}
                      </div>
                      {item.narrative && <p className="timeline__narrative">{item.narrative}</p>}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
