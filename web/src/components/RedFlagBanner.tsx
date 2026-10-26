/** A-1/AC-11.1 红旗提示卡片（规则引擎插入，常驻回答顶部）。 */
export interface RedFlagBannerProps {
  code: string;
  severity: 'high' | 'medium';
  message: string;
}

export default function RedFlagBanner({ severity, message }: RedFlagBannerProps) {
  return (
    <div className={`redflag-banner redflag-banner--${severity}`} role="alert">
      <span className="redflag-banner__icon">{severity === 'high' ? '🚨' : '⚠️'}</span>
      <div>
        <strong>{severity === 'high' ? '立即就医' : '就医建议'}</strong>
        <p>{message}</p>
      </div>
    </div>
  );
}
