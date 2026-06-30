
# ManifestResponse


## Properties

Name | Type
------------ | -------------
`connectorUri` | string
`staging` | string
`needSha1` | Array&lt;string&gt;
`deletionCandidates` | [Array&lt;DeletionCandidate&gt;](DeletionCandidate.md)

## Example

```typescript
import type { ManifestResponse } from '@mfs/sdk'

// TODO: Update the object below with actual values
const example = {
  "connectorUri": null,
  "staging": null,
  "needSha1": null,
  "deletionCandidates": null,
} satisfies ManifestResponse

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ManifestResponse
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


