
# ProbeResponse


## Properties

Name | Type
------------ | -------------
`target` | string
`type` | string
`ok` | boolean
`detail` | string

## Example

```typescript
import type { ProbeResponse } from '@mfs/sdk'

// TODO: Update the object below with actual values
const example = {
  "target": null,
  "type": null,
  "ok": null,
  "detail": null,
} satisfies ProbeResponse

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ProbeResponse
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


