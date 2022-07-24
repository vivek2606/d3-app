export const Marks = ({ data, xScale, yScale, xAccessor, yAccessor, toolTipFormat }) => data.map(d =>
    <rect
      key={yAccessor(d)}
      x={0}
      y={yScale(yAccessor(d))}
      width={xScale(xAccessor(d))}
      height={yScale.bandwidth()}
      className="fill-slate-800 stroke-white">
        <title>{toolTipFormat(xAccessor(d))}</title>
        </rect>)