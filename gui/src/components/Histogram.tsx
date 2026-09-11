import { riskBand } from '../api/schemas';

const BAND_COLOR: Record<string, string> = {
  SAFE: 'var(--green)',
  LOW: 'var(--blue)',
  WARNING: 'var(--yellow)',
  DANGER: 'var(--red)',
};

/**
 * Score distribution. Buckets span 0-1 evenly, so bucket i covers
 * [i/n, (i+1)/n) and is coloured by the risk band its midpoint falls in —
 * the shape tells you at a glance whether traffic is bimodal or smeared
 * across the threshold.
 */
export function Histogram({ buckets }: { buckets: number[] }) {
  const tallest = Math.max(...buckets, 0);
  const width = 1 / buckets.length;

  return (
    <>
      <div className="histogram">
      {buckets.map((count, index) => {
        const from = index * width;
        const to = (index + 1) * width;
        return (
          <div
            key={index}
            data-testid="bar"
            className="histogram__bar"
            title={`${from.toFixed(2)}-${to.toFixed(2)}: ${count}`}
            style={{
              height: tallest > 0 ? `${(count / tallest) * 100}%` : '0%',
              // Empty buckets stay neutral. Colouring them by band drew a
              // rainbow baseline that read as data when it was just the axis.
              background: count > 0 ? BAND_COLOR[riskBand(from + width / 2)] : 'var(--card-border)',
            }}
          />
        );
      })}
      </div>
      <div className="ruler__scale">
        <span>0.00</span>
        <span>0.50</span>
        <span>1.00</span>
      </div>
    </>
  );
}
