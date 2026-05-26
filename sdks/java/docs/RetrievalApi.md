# RetrievalApi

All URIs are relative to *http://127.0.0.1:8765*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**grep**](RetrievalApi.md#grep) | **GET** /v1/grep | Grep |
| [**search**](RetrievalApi.md#search) | **GET** /v1/search | Search |


<a id="grep"></a>
# **grep**
> GrepResponse grep(pattern, path)

Grep

### Example
```java
// Import classes:
import io.zilliz.mfs.ApiClient;
import io.zilliz.mfs.ApiException;
import io.zilliz.mfs.Configuration;
import io.zilliz.mfs.models.*;
import io.zilliz.mfs.api.RetrievalApi;

public class Example {
  public static void main(String[] args) {
    ApiClient defaultClient = Configuration.getDefaultApiClient();
    defaultClient.setBasePath("http://127.0.0.1:8765");

    RetrievalApi apiInstance = new RetrievalApi(defaultClient);
    String pattern = "pattern_example"; // String | 
    String path = "path_example"; // String | 
    try {
      GrepResponse result = apiInstance.grep(pattern, path);
      System.out.println(result);
    } catch (ApiException e) {
      System.err.println("Exception when calling RetrievalApi#grep");
      System.err.println("Status code: " + e.getCode());
      System.err.println("Reason: " + e.getResponseBody());
      System.err.println("Response headers: " + e.getResponseHeaders());
      e.printStackTrace();
    }
  }
}
```

### Parameters

| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **pattern** | **String**|  | |
| **path** | **String**|  | |

### Return type

[**GrepResponse**](GrepResponse.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

<a id="search"></a>
# **search**
> SearchResponse search(q, path, mode, topK, collapse)

Search

### Example
```java
// Import classes:
import io.zilliz.mfs.ApiClient;
import io.zilliz.mfs.ApiException;
import io.zilliz.mfs.Configuration;
import io.zilliz.mfs.models.*;
import io.zilliz.mfs.api.RetrievalApi;

public class Example {
  public static void main(String[] args) {
    ApiClient defaultClient = Configuration.getDefaultApiClient();
    defaultClient.setBasePath("http://127.0.0.1:8765");

    RetrievalApi apiInstance = new RetrievalApi(defaultClient);
    String q = "q_example"; // String | 
    String path = "path_example"; // String | 
    String mode = "hybrid"; // String | 
    Integer topK = 10; // Integer | 
    Boolean collapse = false; // Boolean | 
    try {
      SearchResponse result = apiInstance.search(q, path, mode, topK, collapse);
      System.out.println(result);
    } catch (ApiException e) {
      System.err.println("Exception when calling RetrievalApi#search");
      System.err.println("Status code: " + e.getCode());
      System.err.println("Reason: " + e.getResponseBody());
      System.err.println("Response headers: " + e.getResponseHeaders());
      e.printStackTrace();
    }
  }
}
```

### Parameters

| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **q** | **String**|  | |
| **path** | **String**|  | [optional] |
| **mode** | **String**|  | [optional] [default to hybrid] |
| **topK** | **Integer**|  | [optional] [default to 10] |
| **collapse** | **Boolean**|  | [optional] [default to false] |

### Return type

[**SearchResponse**](SearchResponse.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

