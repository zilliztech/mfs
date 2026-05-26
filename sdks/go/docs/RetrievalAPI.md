# \RetrievalAPI

All URIs are relative to *http://127.0.0.1:8765*

Method | HTTP request | Description
------------- | ------------- | -------------
[**Grep**](RetrievalAPI.md#Grep) | **Get** /v1/grep | Grep
[**Search**](RetrievalAPI.md#Search) | **Get** /v1/search | Search



## Grep

> GrepResponse Grep(ctx).Pattern(pattern).Path(path).Execute()

Grep

### Example

```go
package main

import (
	"context"
	"fmt"
	"os"
	openapiclient "github.com/zilliztech/mfs-sdk-go"
)

func main() {
	pattern := "pattern_example" // string | 
	path := "path_example" // string | 

	configuration := openapiclient.NewConfiguration()
	apiClient := openapiclient.NewAPIClient(configuration)
	resp, r, err := apiClient.RetrievalAPI.Grep(context.Background()).Pattern(pattern).Path(path).Execute()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error when calling `RetrievalAPI.Grep``: %v\n", err)
		fmt.Fprintf(os.Stderr, "Full HTTP response: %v\n", r)
	}
	// response from `Grep`: GrepResponse
	fmt.Fprintf(os.Stdout, "Response from `RetrievalAPI.Grep`: %v\n", resp)
}
```

### Path Parameters



### Other Parameters

Other parameters are passed through a pointer to a apiGrepRequest struct via the builder pattern


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **pattern** | **string** |  | 
 **path** | **string** |  | 

### Return type

[**GrepResponse**](GrepResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints)
[[Back to Model list]](../README.md#documentation-for-models)
[[Back to README]](../README.md)


## Search

> SearchResponse Search(ctx).Q(q).Path(path).Mode(mode).TopK(topK).Collapse(collapse).Execute()

Search

### Example

```go
package main

import (
	"context"
	"fmt"
	"os"
	openapiclient "github.com/zilliztech/mfs-sdk-go"
)

func main() {
	q := "q_example" // string | 
	path := "path_example" // string |  (optional)
	mode := "mode_example" // string |  (optional) (default to "hybrid")
	topK := int32(56) // int32 |  (optional) (default to 10)
	collapse := true // bool |  (optional) (default to false)

	configuration := openapiclient.NewConfiguration()
	apiClient := openapiclient.NewAPIClient(configuration)
	resp, r, err := apiClient.RetrievalAPI.Search(context.Background()).Q(q).Path(path).Mode(mode).TopK(topK).Collapse(collapse).Execute()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error when calling `RetrievalAPI.Search``: %v\n", err)
		fmt.Fprintf(os.Stderr, "Full HTTP response: %v\n", r)
	}
	// response from `Search`: SearchResponse
	fmt.Fprintf(os.Stdout, "Response from `RetrievalAPI.Search`: %v\n", resp)
}
```

### Path Parameters



### Other Parameters

Other parameters are passed through a pointer to a apiSearchRequest struct via the builder pattern


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **q** | **string** |  | 
 **path** | **string** |  | 
 **mode** | **string** |  | [default to &quot;hybrid&quot;]
 **topK** | **int32** |  | [default to 10]
 **collapse** | **bool** |  | [default to false]

### Return type

[**SearchResponse**](SearchResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints)
[[Back to Model list]](../README.md#documentation-for-models)
[[Back to README]](../README.md)

