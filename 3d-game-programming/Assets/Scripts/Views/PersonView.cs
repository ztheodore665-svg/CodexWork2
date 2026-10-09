using PriestsAndDevils.Controllers;
using PriestsAndDevils.Models;
using UnityEngine;

namespace PriestsAndDevils.Views
{
    public sealed class PersonView : MonoBehaviour
    {
        private GameController controller;
        private int personId;
        private SpriteRenderer spriteRenderer;

        public void Initialize(GameController owner, PersonModel person, Sprite sprite)
        {
            controller = owner;
            personId = person.Id;
            spriteRenderer = gameObject.GetComponent<SpriteRenderer>();
            if (spriteRenderer == null) spriteRenderer = gameObject.AddComponent<SpriteRenderer>();
            spriteRenderer.sprite = sprite;
            BoxCollider2D collider = gameObject.GetComponent<BoxCollider2D>();
            if (collider == null) collider = gameObject.AddComponent<BoxCollider2D>();
            collider.size = new Vector2(0.9f, 0.9f);
        }

        public void SetSelected(bool selected)
        {
            if (spriteRenderer != null) spriteRenderer.color = selected ? Color.yellow : Color.white;
        }

        private void OnMouseDown()
        {
            controller?.OnPersonClicked(personId);
        }
    }
}
