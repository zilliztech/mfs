
# JobResponse


## Properties

Name | Type
------------ | -------------
`id` | string
`status` | string
`opKind` | string
`trigger` | string
`error` | string
`totalObjects` | number
`succeededObjects` | number
`failedObjects` | number
`cancelledObjects` | number
`startedAt` | string
`finishedAt` | string

## Example

```typescript
import type { JobResponse } from '@mfs/sdk'

// TODO: Update the object below with actual values
const example = {
  "id": null,
  "status": null,
  "opKind": null,
  "trigger": null,
  "error": null,
  "totalObjects": null,
  "succeededObjects": null,
  "failedObjects": null,
  "cancelledObjects": null,
  "startedAt": null,
  "finishedAt": null,
} satisfies JobResponse

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as JobResponse
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


