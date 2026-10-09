using PriestsAndDevils.Controllers;
using PriestsAndDevils.Models;
using UnityEngine;

namespace PriestsAndDevils.Views
{
    public sealed class PersonView : MonoBehaviour
    {
        private GameController controller;
        private int personId;
        private Renderer meshRenderer;
        private Color baseColor;

        public void Initialize(GameController owner, PersonModel person, Color color)
        {
            controller = owner;
            personId = person.Id;
            meshRenderer = gameObject.GetComponent<Renderer>();
            if (meshRenderer == null) meshRenderer = gameObject.AddComponent<MeshRenderer>();
            baseColor = color;
            Shader unlit = Shader.Find("Unlit/Color");
            if (unlit != null) meshRenderer.material.shader = unlit;
            meshRenderer.material.color = baseColor;
            if (gameObject.GetComponent<Collider>() == null && gameObject.GetComponent<Collider2D>() == null)
            {
                gameObject.AddComponent<BoxCollider>();
            }
        }

        public void SetSelected(bool selected)
        {
            if (meshRenderer != null) meshRenderer.material.color = selected ? Color.yellow : baseColor;
        }

        private void OnMouseDown()
        {
            controller?.OnPersonClicked(personId);
        }
    }
}
