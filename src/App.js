
import { scaleBand, scaleLinear, max, format } from "d3";
import { useData } from "./useData";
import { AxisBottom } from "./AxisBottom";
import { AxisLeft } from "./AxisLeft";
import { Marks } from "./Marks";

const width = 960;
const height = 500;
const margin = { top: 20, right: 30, bottom: 40, left: 220 };

const innerHeight = height - margin.top - margin.right;
const innerWidth = width - margin.left - margin.right;

const xAccessor = d => d.Population;
const yAccessor = d => d.Country;

const siFormat = format(".2s")
const xAxisTickFormat = tickValue => siFormat().replace('G', 'B')
export default function App() {
  const data = useData();

  if (!data) {
    return <pre>Loading ... </pre>
  }
  const xScale = scaleLinear()
    .domain([0, max(data, xAccessor)])
    .range([0, innerWidth])

  const yScale = scaleBand()
    .domain(data.map(yAccessor))
    .range([0, innerHeight])

  return (
    <div>
      <svg width={width} height={height}>
        <g transform={`translate(${margin.left},${margin.top})`}>
          <AxisBottom xScale={xScale}
            innerHeight={innerHeight}
            tickFormat={xAxisTickFormat} />
          <AxisLeft yScale={yScale} />
          <text className="text-sm fill-slate-600"
            textAnchor="middle"
            x={innerWidth / 2}
            y={innerHeight + 25}>Population</text>
          <Marks
            data={data}
            xScale={xScale}
            yScale={yScale}
            xAccessor={xAccessor}
            yAccessor={yAccessor}
            toolTipFormat={xAxisTickFormat} />
        </g>

      </svg>

    </div>

  )
}

