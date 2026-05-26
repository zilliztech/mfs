# \BrowseAPI

All URIs are relative to *http://127.0.0.1:8765*

Method | HTTP request | Description
------------- | ------------- | -------------
[**Cat**](BrowseAPI.md#Cat) | **Get** /v1/cat | Cat
[**Ls**](BrowseAPI.md#Ls) | **Get** /v1/ls | Ls



## Cat

> CatResponse Cat(ctx).Path(path).Range_(range_).Meta(meta).Execute()

Cat

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
	path := "path_example" // string | 
	range_ := "range__example" // string |  (optional)
	meta := true // bool |  (optional) (default to false)

	configuration := openapiclient.NewConfiguration()
	apiClient := openapiclient.NewAPIClient(configuration)
	resp, r, err := apiClient.BrowseAPI.Cat(context.Background()).Path(path).Range_(range_).Meta(meta).Execute()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error when calling `BrowseAPI.Cat``: %v\n", err)
		fmt.Fprintf(os.Stderr, "Full HTTP response: %v\n", r)
	}
	// response from `Cat`: CatResponse
	fmt.Fprintf(os.Stdout, "Response from `BrowseAPI.Cat`: %v\n", resp)
}
```

### Path Parameters



### Other Parameters

Other parameters are passed through a pointer to a apiCatRequest struct via the builder pattern


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **path** | **string** |  | 
 **range_** | **string** |  | 
 **meta** | **bool** |  | [default to false]

### Return type

[**CatResponse**](CatResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints)
[[Back to Model list]](../README.md#documentation-for-models)
[[Back to README]](../README.md)


## Ls

> LsResponse Ls(ctx).Path(path).Execute()

Ls

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
	path := "path_example" // string | 

	configuration := openapiclient.NewConfiguration()
	apiClient := openapiclient.NewAPIClient(configuration)
	resp, r, err := apiClient.BrowseAPI.Ls(context.Background()).Path(path).Execute()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error when calling `BrowseAPI.Ls``: %v\n", err)
		fmt.Fprintf(os.Stderr, "Full HTTP response: %v\n", r)
	}
	// response from `Ls`: LsResponse
	fmt.Fprintf(os.Stdout, "Response from `BrowseAPI.Ls`: %v\n", resp)
}
```

### Path Parameters



### Other Parameters

Other parameters are passed through a pointer to a apiLsRequest struct via the builder pattern


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **path** | **string** |  | 

### Return type

[**LsResponse**](LsResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints)
[[Back to Model list]](../README.md#documentation-for-models)
[[Back to README]](../README.md)

