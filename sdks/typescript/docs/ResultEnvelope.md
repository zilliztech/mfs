
# ResultEnvelope

One search/grep hit (design/06 §7). Outer shape is stable across connectors; locator + metadata.fields are per-connector but documented.

## Properties

Name | Type
------------ | -------------
`source` | string
`content` | string
`score` | number
`locator` | { [key: string]: any; } — body/code/document: `{"lines":[start,end]}`; structured (DB row, issue, slack thread): connector PK dict; once-per-object: null.
`metadata` | { [key: string]: any; }

## Example

```typescript
import type { ResultEnvelope } from '@mfs/sdk'

// TODO: Update the object below with actual values
const example = {
  "source": null,
  "content": null,
  "score": null,
  "locator": null,
  "metadata": null,
} satisfies ResultEnvelope

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ResultEnvelope
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


