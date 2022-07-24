
export const AxisLeft = ({ yScale }) =>
yScale.domain().map(tickValue => (
  <text className="fill-gray-500 text-sm" key={tickValue} x={-3} dy="0.32em" y={yScale(tickValue) + yScale.bandwidth() / 2.0} style={{ textAnchor: "end" }}>{tickValue}</text>
))
