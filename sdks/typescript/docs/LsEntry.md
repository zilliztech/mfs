
# LsEntry


## Properties

Name | Type
------------ | -------------
`name` | string
`type` | string
`mediaType` | string
`sizeHint` | number
`path` | string
`searchStatus` | string
`indexable` | boolean

## Example

```typescript
import type { LsEntry } from '@mfs/sdk'

// TODO: Update the object below with actual values
const example = {
  "name": null,
  "type": null,
  "mediaType": null,
  "sizeHint": null,
  "path": null,
  "searchStatus": null,
  "indexable": null,
} satisfies LsEntry

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as LsEntry
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


