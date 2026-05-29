
# GrepMatchModel


## Properties

Name | Type
------------ | -------------
`source` | string
`locator` | { [key: string]: any; } — text/code line hits: `{"lines":[n,n]}`; structured pushdown: connector PK dict; notice rows: null.
`content` | string
`via` | string

## Example

```typescript
import type { GrepMatchModel } from '@mfs/sdk'

// TODO: Update the object below with actual values
const example = {
  "source": null,
  "locator": null,
  "content": null,
  "via": null,
} satisfies GrepMatchModel

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as GrepMatchModel
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


