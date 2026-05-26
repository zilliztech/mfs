
# CancelResponse


## Properties

Name | Type
------------ | -------------
`jobId` | string
`cancelled` | boolean

## Example

```typescript
import type { CancelResponse } from '@mfs/sdk'

// TODO: Update the object below with actual values
const example = {
  "jobId": null,
  "cancelled": null,
} satisfies CancelResponse

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as CancelResponse
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


