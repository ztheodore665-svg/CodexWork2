package main

import (
	"bytes"
	"crypto/sha256"
	"errors"
	"fmt"

	secp256k1 "github.com/decred/dcrd/dcrec/secp256k1/v4"
	secpECDSA "github.com/decred/dcrd/dcrec/secp256k1/v4/ecdsa"
	"golang.org/x/crypto/ripemd160"
)

const (
	base58Alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
	addressVersion = byte(0x00)
)

// Wallet contains the private key, public key, and the corresponding address.
// The private key must never be broadcast or included in a transaction.
type Wallet struct {
	PrivateKey *secp256k1.PrivateKey
	PublicKey  *secp256k1.PublicKey
	Address    string
}

// TransferData is the part of a transfer that Alice signs.
type TransferData struct {
	To     string
	Amount int64
}

// Message returns a canonical byte representation for signing. Including a
// domain/version prefix prevents this signature from being reused for another
// message type in a larger application.
func (t TransferData) Message() []byte {
	return []byte(fmt.Sprintf("BTC-TRANSFER-V1|to=%s|amount=%d", t.To, t.Amount))
}

// Validate checks the public transaction fields before signing or broadcasting.
func (t TransferData) Validate() error {
	if err := ValidateAddress(t.To); err != nil {
		return fmt.Errorf("invalid recipient address: %w", err)
	}
	if t.Amount <= 0 {
		return errors.New("amount must be greater than zero")
	}
	return nil
}

// NewWallet generates a secp256k1 key pair and a Bitcoin-style Base58Check
// address derived from the compressed public key.
func NewWallet() (*Wallet, error) {
	privateKey, err := secp256k1.GeneratePrivateKey()
	if err != nil {
		return nil, fmt.Errorf("generate private key: %w", err)
	}
	publicKey := privateKey.PubKey()
	address := addressFromPublicKey(publicKey)
	if err := ValidateAddress(address); err != nil {
		return nil, fmt.Errorf("generated invalid address: %w", err)
	}
	return &Wallet{PrivateKey: privateKey, PublicKey: publicKey, Address: address}, nil
}

// Sign hashes message with SHA-256 and returns a DER-encoded ECDSA signature.
func Sign(privateKey *secp256k1.PrivateKey, message []byte) ([]byte, error) {
	if privateKey == nil {
		return nil, errors.New("private key is nil")
	}
	digest := sha256.Sum256(message)
	return secpECDSA.Sign(privateKey, digest[:]).Serialize(), nil
}

// Verify checks whether signature was produced by the private key corresponding
// to publicKey for exactly this message.
func Verify(publicKey *secp256k1.PublicKey, message, signature []byte) (bool, error) {
	if publicKey == nil {
		return false, errors.New("public key is nil")
	}
	if len(signature) == 0 {
		return false, errors.New("signature is empty")
	}
	parsedSignature, err := secpECDSA.ParseDERSignature(signature)
	if err != nil {
		return false, fmt.Errorf("parse DER signature: %w", err)
	}
	digest := sha256.Sum256(message)
	return parsedSignature.Verify(digest[:], publicKey), nil
}

func addressFromPublicKey(publicKey *secp256k1.PublicKey) string {
	publicKeyHash := hash160(publicKey.SerializeCompressed())
	payload := append([]byte{addressVersion}, publicKeyHash...)
	first := sha256.Sum256(payload)
	second := sha256.Sum256(first[:])
	return base58Encode(append(payload, second[:4]...))
}

// ValidateAddress checks the version byte, payload length, and Base58Check
// checksum. This validates the address encoding, not ownership of its funds.
func ValidateAddress(address string) error {
	decoded, err := base58Decode(address)
	if err != nil {
		return err
	}
	if len(decoded) != 25 {
		return fmt.Errorf("address must decode to 25 bytes, got %d", len(decoded))
	}
	if decoded[0] != addressVersion {
		return fmt.Errorf("unsupported address version: %d", decoded[0])
	}
	data := decoded[:21]
	first := sha256.Sum256(data)
	second := sha256.Sum256(first[:])
	if !bytes.Equal(decoded[21:], second[:4]) {
		return errors.New("address checksum mismatch")
	}
	return nil
}

func hash160(data []byte) []byte {
	first := sha256.Sum256(data)
	hash := ripemd160.New()
	_, _ = hash.Write(first[:])
	return hash.Sum(nil)
}

func base58Encode(input []byte) string {
	if len(input) == 0 {
		return ""
	}
	leadingZeros := 0
	for leadingZeros < len(input) && input[leadingZeros] == 0 {
		leadingZeros++
	}
	number := append([]byte(nil), input...)
	encoded := make([]byte, 0, len(input)*2)
	for len(number) > 0 && !(len(number) == 1 && number[0] == 0) {
		quotient := make([]byte, 0, len(number))
		remainder := 0
		for _, digit := range number {
			value := remainder*256 + int(digit)
			if len(quotient) > 0 || value/58 > 0 {
				quotient = append(quotient, byte(value/58))
			}
			remainder = value % 58
		}
		encoded = append(encoded, base58Alphabet[remainder])
		number = quotient
	}
	for i := 0; i < leadingZeros; i++ {
		encoded = append(encoded, base58Alphabet[0])
	}
	for i, j := 0, len(encoded)-1; i < j; i, j = i+1, j-1 {
		encoded[i], encoded[j] = encoded[j], encoded[i]
	}
	return string(encoded)
}

func base58Decode(input string) ([]byte, error) {
	if input == "" {
		return nil, errors.New("address is empty")
	}
	leadingOnes := 0
	for leadingOnes < len(input) && input[leadingOnes] == base58Alphabet[0] {
		leadingOnes++
	}
	number := []byte{}
	for _, character := range input {
		index := bytes.IndexByte([]byte(base58Alphabet), byte(character))
		if index < 0 {
			return nil, fmt.Errorf("invalid Base58 character %q", character)
		}
		carry := index
		for i := len(number) - 1; i >= 0; i-- {
			value := int(number[i])*58 + carry
			number[i] = byte(value)
			carry = value >> 8
		}
		for carry > 0 {
			number = append([]byte{byte(carry)}, number...)
			carry >>= 8
		}
	}
	decoded := make([]byte, leadingOnes, leadingOnes+len(number))
	decoded = append(decoded, number...)
	return decoded, nil
}
