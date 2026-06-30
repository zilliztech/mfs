
# DeletionCandidate


## Properties

Name | Type
------------ | -------------
`path` | string
`size` | number
`inode` | number
`sha1` | string

## Example

```typescript
import type { DeletionCandidate } from '@mfs/sdk'

// TODO: Update the object below with actual values
const example = {
  "path": null,
  "size": null,
  "inode": null,
  "sha1": null,
} satisfies DeletionCandidate

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as DeletionCandidate
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


