
# ManifestRequest


## Properties

Name | Type
------------ | -------------
`clientId` | string
`root` | string
`files` | [Array&lt;ManifestFile&gt;](ManifestFile.md)

## Example

```typescript
import type { ManifestRequest } from '@mfs/sdk'

// TODO: Update the object below with actual values
const example = {
  "clientId": null,
  "root": null,
  "files": null,
} satisfies ManifestRequest

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ManifestRequest
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


