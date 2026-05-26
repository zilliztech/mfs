# BrowseApi

All URIs are relative to *http://127.0.0.1:8765*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**cat**](BrowseApi.md#cat) | **GET** /v1/cat | Cat |
| [**ls**](BrowseApi.md#ls) | **GET** /v1/ls | Ls |


<a id="cat"></a>
# **cat**
> CatResponse cat(path, range, meta, density)

Cat

### Example
```java
// Import classes:
import io.zilliz.mfs.ApiClient;
import io.zilliz.mfs.ApiException;
import io.zilliz.mfs.Configuration;
import io.zilliz.mfs.models.*;
import io.zilliz.mfs.api.BrowseApi;

public class Example {
  public static void main(String[] args) {
    ApiClient defaultClient = Configuration.getDefaultApiClient();
    defaultClient.setBasePath("http://127.0.0.1:8765");

    BrowseApi apiInstance = new BrowseApi(defaultClient);
    String path = "path_example"; // String | 
    String range = "range_example"; // String | 
    Boolean meta = false; // Boolean | 
    String density = "density_example"; // String | 
    try {
      CatResponse result = apiInstance.cat(path, range, meta, density);
      System.out.println(result);
    } catch (ApiException e) {
      System.err.println("Exception when calling BrowseApi#cat");
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
| **path** | **String**|  | |
| **range** | **String**|  | [optional] |
| **meta** | **Boolean**|  | [optional] [default to false] |
| **density** | **String**|  | [optional] |

### Return type

[**CatResponse**](CatResponse.md)

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

<a id="ls"></a>
# **ls**
> LsResponse ls(path)

Ls

### Example
```java
// Import classes:
import io.zilliz.mfs.ApiClient;
import io.zilliz.mfs.ApiException;
import io.zilliz.mfs.Configuration;
import io.zilliz.mfs.models.*;
import io.zilliz.mfs.api.BrowseApi;

public class Example {
  public static void main(String[] args) {
    ApiClient defaultClient = Configuration.getDefaultApiClient();
    defaultClient.setBasePath("http://127.0.0.1:8765");

    BrowseApi apiInstance = new BrowseApi(defaultClient);
    String path = "path_example"; // String | 
    try {
      LsResponse result = apiInstance.ls(path);
      System.out.println(result);
    } catch (ApiException e) {
      System.err.println("Exception when calling BrowseApi#ls");
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
| **path** | **String**|  | |

### Return type

[**LsResponse**](LsResponse.md)

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

