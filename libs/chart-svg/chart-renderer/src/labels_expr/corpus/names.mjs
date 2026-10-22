import {codeGenerator} from 'vega-functions';
import * as vega from 'vega';
const names = Object.keys(codeGenerator.functions).sort();
console.log(names.length);
console.log(JSON.stringify(names));
