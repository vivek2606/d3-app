import { csv } from "d3";
import { useState, useEffect } from "react";


const csvUrl = "https://gist.githubusercontent.com/vivek2606/58b964b3fcd82a741c6d8be06ff3fc64/raw/UN_population.csv"

export const useData = () => {
    const [data, setData] = useState(null);
    useEffect(() => {
      const row = d => {
        d.Population = +d['2020'] * 1000
        return d
      }
      csv(csvUrl, row).then(data => {
        setData(data.slice(0, 10))
      })
    }, []);
    return data;
  }
  