# \ServerAPI

All URIs are relative to *http://127.0.0.1:8765*

Method | HTTP request | Description
------------- | ------------- | -------------
[**GetServerInfo**](ServerAPI.md#GetServerInfo) | **Get** /v1/server/info | Server Info
[**Status**](ServerAPI.md#Status) | **Get** /v1/status | Status



## GetServerInfo

> ServerInfo GetServerInfo(ctx).Execute()

Server Info

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

	configuration := openapiclient.NewConfiguration()
	apiClient := openapiclient.NewAPIClient(configuration)
	resp, r, err := apiClient.ServerAPI.GetServerInfo(context.Background()).Execute()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error when calling `ServerAPI.GetServerInfo``: %v\n", err)
		fmt.Fprintf(os.Stderr, "Full HTTP response: %v\n", r)
	}
	// response from `GetServerInfo`: ServerInfo
	fmt.Fprintf(os.Stdout, "Response from `ServerAPI.GetServerInfo`: %v\n", resp)
}
```

### Path Parameters

This endpoint does not need any parameter.

### Other Parameters

Other parameters are passed through a pointer to a apiGetServerInfoRequest struct via the builder pattern


### Return type

[**ServerInfo**](ServerInfo.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints)
[[Back to Model list]](../README.md#documentation-for-models)
[[Back to README]](../README.md)


## Status

> StatusResponse Status(ctx).Execute()

Status

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

	configuration := openapiclient.NewConfiguration()
	apiClient := openapiclient.NewAPIClient(configuration)
	resp, r, err := apiClient.ServerAPI.Status(context.Background()).Execute()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error when calling `ServerAPI.Status``: %v\n", err)
		fmt.Fprintf(os.Stderr, "Full HTTP response: %v\n", r)
	}
	// response from `Status`: StatusResponse
	fmt.Fprintf(os.Stdout, "Response from `ServerAPI.Status`: %v\n", resp)
}
```

### Path Parameters

This endpoint does not need any parameter.

### Other Parameters

Other parameters are passed through a pointer to a apiStatusRequest struct via the builder pattern


### Return type

[**StatusResponse**](StatusResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints)
[[Back to Model list]](../README.md#documentation-for-models)
[[Back to README]](../README.md)

