
# AddRequest


## Properties

Name | Type
------------ | -------------
`target` | string
`config` | { [key: string]: any; }
`full` | boolean
`since` | string
`process` | boolean
`update` | boolean

## Example

```typescript
import type { AddRequest } from '@mfs/sdk'

// TODO: Update the object below with actual values
const example = {
  "target": null,
  "config": null,
  "full": null,
  "since": null,
  "process": null,
  "update": null,
} satisfies AddRequest

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as AddRequest
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


