# \IngestAPI

All URIs are relative to *http://127.0.0.1:8765*

Method | HTTP request | Description
------------- | ------------- | -------------
[**AddSource**](IngestAPI.md#AddSource) | **Post** /v1/add | Add
[**GetJob**](IngestAPI.md#GetJob) | **Get** /v1/jobs/{job_id} | Job



## AddSource

> AddResponse AddSource(ctx).AddRequest(addRequest).Execute()

Add

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
	addRequest := *openapiclient.NewAddRequest("Target_example") // AddRequest | 

	configuration := openapiclient.NewConfiguration()
	apiClient := openapiclient.NewAPIClient(configuration)
	resp, r, err := apiClient.IngestAPI.AddSource(context.Background()).AddRequest(addRequest).Execute()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error when calling `IngestAPI.AddSource``: %v\n", err)
		fmt.Fprintf(os.Stderr, "Full HTTP response: %v\n", r)
	}
	// response from `AddSource`: AddResponse
	fmt.Fprintf(os.Stdout, "Response from `IngestAPI.AddSource`: %v\n", resp)
}
```

### Path Parameters



### Other Parameters

Other parameters are passed through a pointer to a apiAddSourceRequest struct via the builder pattern


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **addRequest** | [**AddRequest**](AddRequest.md) |  | 

### Return type

[**AddResponse**](AddResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: application/json
- **Accept**: application/json

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints)
[[Back to Model list]](../README.md#documentation-for-models)
[[Back to README]](../README.md)


## GetJob

> JobResponse GetJob(ctx, jobId).Execute()

Job

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
	jobId := "jobId_example" // string | 

	configuration := openapiclient.NewConfiguration()
	apiClient := openapiclient.NewAPIClient(configuration)
	resp, r, err := apiClient.IngestAPI.GetJob(context.Background(), jobId).Execute()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error when calling `IngestAPI.GetJob``: %v\n", err)
		fmt.Fprintf(os.Stderr, "Full HTTP response: %v\n", r)
	}
	// response from `GetJob`: JobResponse
	fmt.Fprintf(os.Stdout, "Response from `IngestAPI.GetJob`: %v\n", resp)
}
```

### Path Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
**ctx** | **context.Context** | context for authentication, logging, cancellation, deadlines, tracing, etc.
**jobId** | **string** |  | 

### Other Parameters

Other parameters are passed through a pointer to a apiGetJobRequest struct via the builder pattern


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------


### Return type

[**JobResponse**](JobResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints)
[[Back to Model list]](../README.md#documentation-for-models)
[[Back to README]](../README.md)

