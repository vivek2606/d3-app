export const AxisBottom = ({ xScale, innerHeight, tickFormat }) =>
  xScale.ticks().map(tickValue => (
    <g key={tickValue} transform={`translate(${xScale(tickValue)}, 0)`}>
      <line y2={innerHeight} className="stroke-slate-200" strokeDasharray={5} />
      <text className="text-sm fill-gray-500" 
            y={innerHeight + 3} 
            style={{ textAnchor: 'middle' }} 
            dy=".71em">{tickFormat(tickValue)}</text>
    </g>
  ));

