# LsEntry

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**Name** | **string** |  | 
**Type** | **string** | file | dir | 
**MediaType** | Pointer to **NullableString** |  | [optional] 
**SizeHint** | Pointer to **NullableInt32** |  | [optional] 

## Methods

### NewLsEntry

`func NewLsEntry(name string, type_ string, ) *LsEntry`

NewLsEntry instantiates a new LsEntry object
This constructor will assign default values to properties that have it defined,
and makes sure properties required by API are set, but the set of arguments
will change when the set of required properties is changed

### NewLsEntryWithDefaults

`func NewLsEntryWithDefaults() *LsEntry`

NewLsEntryWithDefaults instantiates a new LsEntry object
This constructor will only assign default values to properties that have it defined,
but it doesn't guarantee that properties required by API are set

### GetName

`func (o *LsEntry) GetName() string`

GetName returns the Name field if non-nil, zero value otherwise.

### GetNameOk

`func (o *LsEntry) GetNameOk() (*string, bool)`

GetNameOk returns a tuple with the Name field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetName

`func (o *LsEntry) SetName(v string)`

SetName sets Name field to given value.


### GetType

`func (o *LsEntry) GetType() string`

GetType returns the Type field if non-nil, zero value otherwise.

### GetTypeOk

`func (o *LsEntry) GetTypeOk() (*string, bool)`

GetTypeOk returns a tuple with the Type field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetType

`func (o *LsEntry) SetType(v string)`

SetType sets Type field to given value.


### GetMediaType

`func (o *LsEntry) GetMediaType() string`

GetMediaType returns the MediaType field if non-nil, zero value otherwise.

### GetMediaTypeOk

`func (o *LsEntry) GetMediaTypeOk() (*string, bool)`

GetMediaTypeOk returns a tuple with the MediaType field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetMediaType

`func (o *LsEntry) SetMediaType(v string)`

SetMediaType sets MediaType field to given value.

### HasMediaType

`func (o *LsEntry) HasMediaType() bool`

HasMediaType returns a boolean if a field has been set.

### SetMediaTypeNil

`func (o *LsEntry) SetMediaTypeNil(b bool)`

 SetMediaTypeNil sets the value for MediaType to be an explicit nil

### UnsetMediaType
`func (o *LsEntry) UnsetMediaType()`

UnsetMediaType ensures that no value is present for MediaType, not even an explicit nil
### GetSizeHint

`func (o *LsEntry) GetSizeHint() int32`

GetSizeHint returns the SizeHint field if non-nil, zero value otherwise.

### GetSizeHintOk

`func (o *LsEntry) GetSizeHintOk() (*int32, bool)`

GetSizeHintOk returns a tuple with the SizeHint field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetSizeHint

`func (o *LsEntry) SetSizeHint(v int32)`

SetSizeHint sets SizeHint field to given value.

### HasSizeHint

`func (o *LsEntry) HasSizeHint() bool`

HasSizeHint returns a boolean if a field has been set.

### SetSizeHintNil

`func (o *LsEntry) SetSizeHintNil(b bool)`

 SetSizeHintNil sets the value for SizeHint to be an explicit nil

### UnsetSizeHint
`func (o *LsEntry) UnsetSizeHint()`

UnsetSizeHint ensures that no value is present for SizeHint, not even an explicit nil

[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


